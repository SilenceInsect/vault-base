#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T12 回归：per-skill 数据根 <vault-base>/<skill_id>/ 全链路。

假环境：%TEMP%/vb-t12-*/repo 作为 COMMON_SKILLS_REPO（数据全部落假仓）；
被测代码本体 = 本仓 scripts/skill_vault_adapt.py（import）与真实 hooks（subprocess）。

覆盖场景：
  A  双实体副本 adapt → 注入/单点数据 → 幂等 → uninstall 字节级往返（sediment 保留）
  B  昨日过渡布局 references/vault-integration/ 自动迁移 + 空树剪枝
  C  v1 legacy 注入迁移（模板一致删除 / 改过保留 / v1 节升级 / 空目录剪枝）
  D  RESERVED_ROOT_NAMES 守卫（skill_id 撞包目录名 → 拒绝）
  E  junction 识别（best-effort，建不了则 SKIP）
  F  hooks 落点（post 沉淀新根；pre 解析新根清单）

跑法：python tests/test_vault_adapt_layout.py ；exit 0 = 全过。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REAL_VB = Path(__file__).resolve().parents[1]          # vault-base 包根（真实仓）
SCRIPTS = REAL_VB / "scripts"

TMP = Path(tempfile.mkdtemp(prefix="vb-t12-"))
REPO = TMP / "repo"                                    # 假 common-skills-repo
FAKE_VB = REPO / "vault-base"                          # 假 vault 根（数据落点）

os.environ["COMMON_SKILLS_REPO"] = str(REPO)           # 须在 import sva 前
sys.path.insert(0, str(SCRIPTS))

import skill_vault_adapt as sva                        # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(cond), detail))
    print("%s  %s%s" % ("PASS" if cond else "FAIL", name,
                        ("  | " + detail) if detail else ""))
    return bool(cond)


def setup_fake_vault() -> None:
    """假仓 vault-base：adapt_assets 模板/hooks + pre hook 所需 scripts 三件套。"""
    FAKE_VB.mkdir(parents=True, exist_ok=True)
    src = REAL_VB / "assets" / "skill-adapt"
    dst = FAKE_VB / "assets" / "skill-adapt"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    sdir = FAKE_VB / "scripts"
    sdir.mkdir(parents=True, exist_ok=True)
    for f in ("vault_paths.py", "vault_secrets.py", "vault_prefix_cache.py"):
        shutil.copy2(SCRIPTS / f, sdir / f)


SKILL_MD = (
    "---\n"
    'name: %s\n'
    'description: "回归假 skill"\n'
    "---\n"
    "\n"
    "# %s\n"
    "\n"
    "正文一行。\n"
)


def make_skill(root: Path, skill_id: str, crlf: bool = False) -> Path:
    """crlf=True 时生成 CRLF 风格文件（覆盖两种换行的字节级往返）。"""
    d = root / skill_id
    d.mkdir(parents=True, exist_ok=True)
    text = SKILL_MD % (skill_id, skill_id)
    if crlf:
        text = text.replace("\n", "\r\n")
    (d / "SKILL.md").write_bytes(text.encode("utf-8"))
    return d


def copies_of(*dirs: Path, source: str = "ide:codex") -> list[dict]:
    return [{
        "path": str(d), "real": str(d.resolve()),
        "is_link": False, "sources": [source],
    } for d in dirs]


def skill_md_bytes(d: Path) -> bytes:
    return (d / "SKILL.md").read_bytes()


# ---------------------------------------------------------------- 场景 A + F(post)
def scenario_a() -> Path:
    print("\n== A 双实体副本 adapt / 幂等 / uninstall 字节级往返 ==")
    c1 = make_skill(TMP / "skillsA", "grill-me")           # LF 风格
    c2 = make_skill(TMP / "skillsB", "grill-me", crlf=True)  # CRLF 风格
    raw1, raw2 = skill_md_bytes(c1), skill_md_bytes(c2)

    r = sva.adapt_skill("grill-me", copies_of(c1, c2))
    check("A1 adapt ok", r.get("ok"))
    t1, t2 = (c1 / "SKILL.md").read_text(encoding="utf-8"), \
             (c2 / "SKILL.md").read_text(encoding="utf-8")
    check("A2 两副本注入 marker 节+frontmatter",
          all(sva.MARK_BEGIN in t and "accepts_brief: true" in t and "vault_base:" in t
              for t in (t1, t2)))
    pf = FAKE_VB / "grill-me" / "preflight.yml"
    rec = FAKE_VB / "grill-me" / "adapted.json"
    check("A3 preflight 单份落新根", pf.is_file(), str(pf))
    recj = json.loads(rec.read_text(encoding="utf-8")) if rec.is_file() else {}
    check("A4 账本 copies=2 且 schema=2",
          recj.get("schema") == 2 and len(recj.get("copies") or []) == 2,
          json.dumps(recj.get("copies") or [], ensure_ascii=False))
    vb_posix = str(FAKE_VB.resolve().as_posix())
    check("A5 路由节引用新根路径（无 vault-integration）",
          ("<vb>/grill-me/preflight.yml" in t1)
          and ("`<vb>` = %s" % vb_posix) in t1
          and "vault-integration" not in t1)

    # 幂等
    sva.adapt_skill("grill-me", copies_of(c1, c2))
    check("A6 幂等：marker 节仅一份", (c1 / "SKILL.md").read_text(
        encoding="utf-8").count(sva.MARK_BEGIN) == 1)

    # F(post)：沉淀落新根（产出供 A9 保留验证）
    print("\n== F(post) 沉淀钩子落点 ==")
    cp = subprocess.run([
        sys.executable, str(REAL_VB / "assets" / "skill-adapt" / "hooks"
                            / "post_task_sediment.py"),
        "--skill-dir", str(c1), "--skill-id", "grill-me",
        "--vault-base", str(FAKE_VB),
        "--kind", "reusable_decision", "--title", "T12 落点",
        "--answer", "沉淀应落 <vb>/<skill_id>/sediment/",
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        out = json.loads(cp.stdout)
    except Exception:
        out = {}
    check("F1 post hook ok", cp.returncode == 0 and out.get("ok"),
          (cp.stderr or "")[-300:])
    yml = Path(out.get("yaml", "x:"))
    check("F2 沉淀文件在 <vb>/grill-me/sediment/",
          yml.is_file() and yml.parent == (FAKE_VB / "grill-me" / "sediment"),
          str(yml))

    # uninstall
    u = sva.uninstall_skill("grill-me", copies_of(c1, c2))
    check("A7 uninstall ok", u.get("ok", True) is not False)
    check("A8 两副本字节级还原（LF 与 CRLF 各自保持）",
          skill_md_bytes(c1) == raw1 and skill_md_bytes(c2) == raw2)
    check("A9 preflight/账本删除，sediment 保留",
          not pf.exists() and not rec.exists() and yml.is_file())
    return c1


# ---------------------------------------------------------------- 场景 B + F(pre)
def scenario_b() -> None:
    print("\n== B 过渡布局 references/vault-integration/ 自动迁移 ==")
    it = FAKE_VB / "references" / "vault-integration"
    (it / "preflight").mkdir(parents=True, exist_ok=True)
    (it / "adapted").mkdir(parents=True, exist_ok=True)
    (it / "sediment" / "foo").mkdir(parents=True, exist_ok=True)
    (it / "preflight" / "foo.preflight.yml").write_text(
        'skill_id: "foo"\nrecall_query: "foo 召回"\n  recalled_uuids: []\n',
        encoding="utf-8")
    (it / "adapted" / "foo.json").write_text(json.dumps({
        "schema": 1, "skill_id": "foo",
        "skill_path": str((TMP / "skillsC" / "foo").resolve()),
        "adapted_at": "2026-09-29T00:00:00",
    }), encoding="utf-8")
    (it / "sediment" / "foo" / "old.yml").write_text("kind: x\n", encoding="utf-8")

    c3 = make_skill(TMP / "skillsC", "foo")
    r = sva.adapt_skill("foo", copies_of(c3, source="ide:workbuddy"))
    check("B1 adapt ok", r.get("ok"))
    check("B2 preflight 迁到新根", (FAKE_VB / "foo" / "preflight.yml").is_file())
    recj = json.loads((FAKE_VB / "foo" / "adapted.json").read_text(encoding="utf-8"))
    check("B3 账本合并 legacy skill_path 且 schema=2",
          recj.get("schema") == 2
          and any("skillsC" in (c.get("path") or "") for c in recj.get("copies") or []))
    check("B4 sediment 迁到新根",
          (FAKE_VB / "foo" / "sediment" / "old.yml").is_file())
    check("B5 过渡目录空树剪枝", not it.exists())

    # F(pre)：清单解析命中新根
    print("\n== F(pre) 召回钩子清单解析 ==")
    cp = subprocess.run([
        sys.executable, str(REAL_VB / "assets" / "skill-adapt" / "hooks"
                            / "pre_task_recall.py"),
        "--skill-dir", str(c3), "--skill-id", "foo", "--vault-base", str(FAKE_VB),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        out = json.loads(cp.stdout)
    except Exception:
        out = {}
    check("F3 pre hook ok", cp.returncode == 0 and out.get("ok"),
          (cp.stderr or "")[-300:])
    check("F4 清单解析命中新根 preflight.yml",
          out.get("checklist") == str(FAKE_VB / "foo" / "preflight.yml"),
          str(out.get("checklist")))


# ---------------------------------------------------------------- 场景 C
def scenario_c() -> None:
    print("\n== C v1 legacy 注入迁移 ==")
    c4 = make_skill(TMP / "skillsD", "bar")
    li = c4 / "references" / "vault-integration"
    li.mkdir(parents=True)
    shutil.copy2(FAKE_VB / "assets" / "skill-adapt" / "knowledge-taxonomy.yml",
                 li / "knowledge-taxonomy.yml")
    (c4 / "hooks").mkdir()
    shutil.copy2(FAKE_VB / "assets" / "skill-adapt" / "hooks" / "pre_task_recall.py",
                 c4 / "hooks" / "pre_task_recall.py")
    (c4 / "hooks" / "post_task_sediment.py").write_text(
        "# 用户改过的版本\n", encoding="utf-8")
    with (c4 / "SKILL.md").open("a", encoding="utf-8") as f:
        f.write("\n## 知识金库接入（vault-base 适配）\n\n旧格式内容\n")

    r = sva.adapt_skill("bar", copies_of(c4))
    check("C1 adapt ok", r.get("ok"))
    t = (c4 / "SKILL.md").read_text(encoding="utf-8")
    check("C2 v1 节升级为 marker 节",
          sva.MARK_BEGIN in t and "旧格式内容" not in t)
    check("C3 模板一致的 taxonomy/hooks 已删",
          not (li / "knowledge-taxonomy.yml").exists()
          and not (c4 / "hooks" / "pre_task_recall.py").exists())
    check("C4 被改过的文件保留",
          (c4 / "hooks" / "post_task_sediment.py").is_file())
    check("C5 legacy 空目录剪枝", not li.exists())


# ---------------------------------------------------------------- 场景 D / E
def scenario_d() -> None:
    print("\n== D RESERVED_ROOT_NAMES 守卫 ==")
    c5 = make_skill(TMP / "skillsE", "scripts")
    r = sva.adapt_skill("scripts", copies_of(c5))
    check("D1 skill_id=scripts 被拒绝",
          not r.get("ok") and "同名" in (r.get("error") or ""),
          r.get("error") or "")
    check("D2 未生成数据目录", not (FAKE_VB / "scripts" / "adapted.json").exists())


def scenario_e() -> None:
    print("\n== E junction 识别（best-effort）==")
    target = make_skill(TMP / "skillsF", "baz")
    link = TMP / "skillsG" / "baz"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        cp = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True)
        if cp.returncode != 0 or not link.exists():
            raise OSError((cp.stdout or "") + (cp.stderr or ""))
        check("E1 junction 识别为联接", sva._path_is_link(link) is True)
        check("E2 实体目录非联接", sva._path_is_link(target) is False)
        real = link.resolve()
        check("E3 junction resolve 到真相源",
              str(real).lower() == str(target.resolve()).lower(), str(real))
    except Exception as e:  # noqa: BLE001
        print("SKIP  E junction 创建失败（环境限制，真机 scan 已验证）: %s" % e)


def main() -> int:
    print("TMP = %s" % TMP)
    setup_fake_vault()
    scenario_a()
    scenario_b()
    scenario_c()
    scenario_d()
    scenario_e()

    fails = [x for x in RESULTS if not x[1]]
    print("\n========================================")
    print("TOTAL %d / PASS %d / FAIL %d" % (len(RESULTS),
          len(RESULTS) - len(fails), len(fails)))
    if fails:
        for name, _, detail in fails:
            print("  FAIL: %s %s" % (name, detail))
    try:
        shutil.rmtree(TMP, ignore_errors=True)
    except OSError:
        pass
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
