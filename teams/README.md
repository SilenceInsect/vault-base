# 项目 / 组私有工作区（team-type:team-name）

逻辑标识：`{team-type}:{team-name}`  
例：`demo-team:demo-project`

磁盘路径（Windows 不能用 `:` 做目录名）：

```text
teams/{team-type}/{team-name}/
→ teams/demo-team/demo-project/
```

## 用途

放**本项目组私有**、不宜随 skill 源码进 SVN/Git 的信息，例如：

- 组内联系人 / 值班
- 本机或组内路径约定、环境备注
- 组级 secrets 指针（真实密码仍只放 `references/vault/_kb/secrets.local.json`）
- 组内 brief 草稿、临时清单

**不要**把可复用的公共知识写在这里——公共知识走 Vault `answers|decisions|references` → 共享审核链路。

## 新建一组

```bash
python scripts/team_workspace.py init --id "demo-team:demo-project"
# 或
python scripts/team_workspace.py init --type demo-team --name demo-project
```

会创建目录、从 `_template/` 拷贝 `team.yml`，并尽量补齐忽略规则。

## 忽略规则

- **Git**：根目录 `.gitignore` 忽略 `teams/**`，但保留本 README 与 `_template/`。
- **SVN**：对 `teams/` 设置 `svn:ignore=*`（已版本管理的 README / `_template` 不受影响；新建的 `demo-team/` 等不会被误提交）。

```bash
python scripts/team_workspace.py fix-ignores
```

## team.yml 字段

见 `_template/team.yml`。`id` 必须等于逻辑名 `team-type:team-name`。
