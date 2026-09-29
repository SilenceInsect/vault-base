#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""laya_client.py — Laya 本地决策模型客户端（System One / Jev 兼容载荷）。

默认对接本机 ``laya-serve``：``POST {base_url}/v1/systemone``。
契约见 references/laya.md。

配置：vault.config.json → laya.*（兼容旧键 jev.*）
凭据：secrets.local.json → laya.api_key（本地 loopback 默认可空）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault_paths import load_config, vault_root                                      # noqa: E402
from vault_secrets import load_secrets                                              # noqa: E402

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_MODEL = "typed-decisions"
DEFAULT_API_PATH = "/v1/systemone"

DEFAULT_LAYA_CFG = {
    "enabled": False,
    "model": DEFAULT_MODEL,
    "base_url": DEFAULT_BASE_URL,
    "api_path": DEFAULT_API_PATH,
    "timeout_seconds": 30,
    "share_min_confidence": 0.75,
    "sensitive_noul_threshold": 0.70,
    "mock": False,
    "require_api_key": False,
}


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def _is_loopback(base_url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    except Exception:
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def laya_config(root: Path | None = None) -> dict:
    cfg = load_config(root)
    out = dict(DEFAULT_LAYA_CFG)
    # 新键优先；旧 jev 段作迁移回落
    section = cfg.get("laya") if isinstance(cfg.get("laya"), dict) else None
    if section is None and isinstance(cfg.get("jev"), dict):
        section = dict(cfg["jev"])
        # 旧默认指向托管 decisions，迁到本地 systemone
        if section.get("base_url") in (None, "", "https://openclaw-api.com"):
            section["base_url"] = DEFAULT_BASE_URL
        if section.get("model") in (None, "", "jev"):
            section["model"] = DEFAULT_MODEL
        section.setdefault("api_path", DEFAULT_API_PATH)
    if isinstance(section, dict):
        out.update(section)

    if _truthy("VAULT_LAYA_ENABLED") or _truthy("VAULT_JEV_ENABLED"):
        out["enabled"] = True
    if _truthy("VAULT_LAYA_MOCK") or _truthy("VAULT_JEV_MOCK"):
        out["mock"] = True
    if os.environ.get("VAULT_LAYA_BASE_URL") or os.environ.get("VAULT_JEV_BASE_URL"):
        out["base_url"] = (
            os.environ.get("VAULT_LAYA_BASE_URL")
            or os.environ.get("VAULT_JEV_BASE_URL")
            or out["base_url"]
        ).rstrip("/")
    if os.environ.get("VAULT_LAYA_MODEL") or os.environ.get("VAULT_JEV_MODEL"):
        out["model"] = (
            os.environ.get("VAULT_LAYA_MODEL")
            or os.environ.get("VAULT_JEV_MODEL")
            or out["model"]
        )
    if os.environ.get("VAULT_LAYA_API_PATH"):
        out["api_path"] = os.environ["VAULT_LAYA_API_PATH"]
    return out


def laya_credentials(root: Path | None = None) -> dict:
    sec_all = load_secrets(root)
    sec = sec_all.get("laya") or sec_all.get("jev") or {}
    api_key = (
        os.environ.get("VAULT_LAYA_API_KEY")
        or os.environ.get("VAULT_JEV_API_KEY")
        or sec.get("api_key")
        or sec.get("token")
        or ""
    )
    base = (
        os.environ.get("VAULT_LAYA_BASE_URL")
        or os.environ.get("VAULT_JEV_BASE_URL")
        or sec.get("base_url")
        or laya_config(root).get("base_url")
        or DEFAULT_BASE_URL
    )
    return {"api_key": api_key.strip(), "base_url": str(base).rstrip("/")}


class LayaClient:
    def __init__(self, root: Path | None = None, *, mock: bool | None = None):
        self.root = root or vault_root()
        self.cfg = laya_config(self.root)
        creds = laya_credentials(self.root)
        self.api_key = creds["api_key"]
        self.base_url = creds["base_url"]
        self.model = self.cfg.get("model") or DEFAULT_MODEL
        self.api_path = self.cfg.get("api_path") or DEFAULT_API_PATH
        if not str(self.api_path).startswith("/"):
            self.api_path = "/" + str(self.api_path)
        self.timeout = float(self.cfg.get("timeout_seconds") or 30)
        if mock is None:
            mock = bool(self.cfg.get("mock"))
        self.mock = bool(mock)

    @property
    def available(self) -> bool:
        if self.mock:
            return True
        if not self.cfg.get("enabled"):
            return False
        require_key = bool(self.cfg.get("require_api_key"))
        if require_key:
            return bool(self.api_key)
        # 本地 laya-serve 默认可无 key；非 loopback 建议配置 key
        if self.api_key:
            return True
        return _is_loopback(self.base_url)

    def decide(self, state: Any, questions: dict) -> dict:
        """调用 System One / Decisions。返回上游 JSON；失败抛 LayaError。"""
        if self.mock:
            return self._mock_decide(state, questions)
        if bool(self.cfg.get("require_api_key")) and not self.api_key:
            raise LayaError(
                "missing api_key (secrets.laya.api_key or VAULT_LAYA_API_KEY)"
            )
        body = {
            "model": self.model,
            "state": state,
            "questions": questions,
        }
        url = self.base_url.rstrip("/") + self.api_path
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = "Bearer %s" % self.api_key
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            raise LayaError("HTTP %s: %s" % (e.code, detail)) from e
        except Exception as e:
            raise LayaError("%s: %s" % (type(e).__name__, e)) from e
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise LayaError("invalid JSON response: %s" % raw[:200]) from e

    def _mock_decide(self, state: Any, questions: dict) -> dict:
        """无服务时的本地模拟：基于简单关键词，便于冒烟。"""
        blob = json.dumps(state, ensure_ascii=False).lower()
        answers = {}
        for qid, q in (questions or {}).items():
            qtype = (q or {}).get("type") or "noul"
            if qtype == "noul":
                sens = any(k in blob for k in ("password", "secret", "api_key", "密码", "密钥"))
                answers[qid] = {"type": "noul", "noul": 0.92 if sens else 0.08}
            elif qtype == "choice":
                criteria = (q or {}).get("criteria") or {}
                keys = list(criteria.keys()) if isinstance(criteria, dict) else []
                if "private" in keys and any(k in blob for k in ("内部", "private", "勿共享")):
                    choice = "private"
                elif "share" in keys and any(k in blob for k in ("共享", "团队", "规范", "决策")):
                    choice = "share"
                elif "needs_human" in keys:
                    choice = "needs_human"
                else:
                    choice = keys[0] if keys else "needs_human"
                probs = {k: (0.85 if k == choice else 0.05) for k in keys} or {choice: 0.85}
                answers[qid] = {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": probs,
                    "confidence": 0.85,
                }
            elif qtype == "score":
                answers[qid] = {
                    "type": "score",
                    "score": 1.0,
                    "probabilities": {"0": 0.1, "1": 0.8, "2": 0.1},
                    "confidence": 0.7,
                }
            else:
                answers[qid] = {"type": qtype, "raw": None}
        return {"model": "mock/laya", "answers": answers, "usage": {"mock": True}}

    def share_gate(self, meta: dict, segments: dict) -> dict:
        """共享候选门控：一次问 share_decision + is_sensitive。"""
        state = {
            "skill_id": meta.get("skill_id") or "",
            "doc_type": meta.get("doc_type") or "answer",
            "title": meta.get("title") or "",
            "question": (segments or {}).get("question") or "",
            "answer": (segments or {}).get("answer") or "",
            "basis": (segments or {}).get("basis") or "",
            "remark": (segments or {}).get("remark") or "",
            "tags": meta.get("tags") or [],
            "is_candidate_shared": bool(meta.get("is_candidate_shared")),
        }
        questions = share_gate_questions()
        try:
            raw = self.decide(state, questions)
        except LayaError as e:
            return {
                "verdict": "uncertain",
                "needs_llm": "1",
                "confidence": 0.0,
                "llm_verdict": "uncertain",
                "llm_confidence": 0.0,
                "llm_reason": "laya_error:%s" % e,
                "answers": {},
                "degraded": True,
                "error": str(e),
            }

        answers = raw.get("answers") or {}
        share_ans = answers.get("share_decision") or {}
        sens_ans = answers.get("is_sensitive") or {}
        choice = share_ans.get("choice") or "needs_human"
        conf = float(share_ans.get("confidence") or 0.0)
        sens = float(sens_ans.get("noul") or 0.0)

        min_conf = float(self.cfg.get("share_min_confidence") or 0.75)
        sens_th = float(self.cfg.get("sensitive_noul_threshold") or 0.70)

        if sens >= sens_th:
            verdict, needs = "private", "0"
            reason = "laya:is_sensitive=%.2f>=%.2f" % (sens, sens_th)
        elif choice == "private" and conf >= min_conf:
            verdict, needs = "private", "0"
            reason = "laya:share_decision=private conf=%.2f" % conf
        elif choice == "share" and conf >= min_conf:
            verdict, needs = "share", "0"
            reason = "laya:share_decision=share conf=%.2f" % conf
        else:
            verdict, needs = "uncertain", "1"
            reason = "laya:low_conf_or_needs_human choice=%s conf=%.2f sens=%.2f" % (
                choice, conf, sens,
            )

        return {
            "verdict": verdict,
            "needs_llm": needs,
            "confidence": conf,
            "llm_verdict": choice if choice in ("share", "private", "uncertain")
                           else ("uncertain" if choice == "needs_human" else choice),
            "llm_confidence": conf,
            "llm_reason": reason,
            "sensitive_noul": sens,
            "answers": answers,
            "degraded": False,
            "usage": raw.get("usage"),
            "model": raw.get("model"),
        }


def share_gate_questions() -> dict:
    return {
        "share_decision": {
            "type": "choice",
            "instructions": (
                "Should this knowledge note be shared to the team vault as a reusable "
                "answer/decision? Choose private (keep local only), share (candidate for "
                "shared vault pending review), or needs_human (ambiguous / needs review)."
            ),
            "criteria": {
                "private": "Personal, sensitive, incomplete, or not reusable",
                "share": "Reusable team knowledge worth pending review",
                "needs_human": "Ambiguous; human or session agent should decide",
            },
        },
        "is_sensitive": {
            "type": "noul",
            "instructions": (
                "Does the note contain secrets, credentials, private hostnames/passwords, "
                "or other content that must NOT be shared?"
            ),
        },
    }


class LayaError(RuntimeError):
    pass


def open_laya(root: Path | None = None, *, mock: bool | None = None) -> LayaClient:
    return LayaClient(root, mock=mock)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Laya 决策客户端（本地 System One）")
    ap.add_argument("--vault")
    ap.add_argument("--mock", action="store_true", help="不调服务，本地关键词模拟")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", help="检查配置/凭据（不强制远程）")
    p_share = sub.add_parser("share-gate", help="对一条笔记做共享门控判定")
    p_share.add_argument("--mock", action="store_true", help="同全局 --mock")
    p_share.add_argument("--title", default="")
    p_share.add_argument("--question", default="")
    p_share.add_argument("--answer", default="")
    p_share.add_argument("--basis", default="")
    p_share.add_argument("--skill-id", default="vault-base")
    p_share.add_argument("--candidate-shared", action="store_true")

    p_dec = sub.add_parser("decide", help="裸调用 api_path（默认 /v1/systemone）")
    p_dec.add_argument("--mock", action="store_true", help="同全局 --mock")
    p_dec.add_argument("--state-json", required=True)
    p_dec.add_argument("--questions-json", required=True)

    args = ap.parse_args()
    root = vault_root(args.vault) if args.vault else vault_root()
    use_mock = bool(getattr(args, "mock", False))
    client = open_laya(root, mock=True if use_mock else None)

    if args.cmd == "probe":
        creds = laya_credentials(root)
        cfg = laya_config(root)
        out = {
            "enabled": bool(cfg.get("enabled")),
            "mock": client.mock,
            "available": client.available,
            "base_url": client.base_url,
            "api_path": client.api_path,
            "model": client.model,
            "api_key_set": bool(creds.get("api_key")),
            "loopback": _is_loopback(client.base_url),
            "share_min_confidence": cfg.get("share_min_confidence"),
            "sensitive_noul_threshold": cfg.get("sensitive_noul_threshold"),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if (client.available or not cfg.get("enabled")) else 1

    if args.cmd == "share-gate":
        meta = {
            "skill_id": args.skill_id,
            "doc_type": "answer",
            "title": args.title or "untitled",
            "is_candidate_shared": bool(args.candidate_shared) or True,
            "tags": [],
        }
        segments = {
            "question": args.question,
            "answer": args.answer,
            "basis": args.basis,
            "remark": "",
        }
        if not client.available and not client.mock:
            print(json.dumps({
                "error": "laya not available",
                "hint": (
                    "set laya.enabled=true + start laya-serve on base_url, "
                    "or pass --mock"
                ),
            }, ensure_ascii=False, indent=2))
            return 2
        if not client.cfg.get("enabled") and not client.mock:
            client.mock = True
        report = client.share_gate(meta, segments)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "decide":
        state = json.loads(args.state_json)
        questions = json.loads(args.questions_json)
        if not client.available and not client.mock:
            print(json.dumps({"error": "laya not available"}, ensure_ascii=False))
            return 2
        if not client.cfg.get("enabled") and not client.mock:
            client.mock = True
        try:
            print(json.dumps(client.decide(state, questions), ensure_ascii=False, indent=2))
            return 0
        except LayaError as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
            return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
