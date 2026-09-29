# 项目组作用域（team-type:team-name）

| 逻辑 ID | 磁盘路径 |
|---------|----------|
| `{team_type}:{team_name}` | `teams/{team_type}/{team_name}/` |
| 例 `test-team:amrd-test` | `teams/test-team/amrd-test/` |

必有文件：`team.yml`（`id` 必须等于逻辑 ID）。

忽略：Git 见 skill 根 `.gitignore`；SVN 对 `teams/` 设 `svn:ignore=*`（见 `team_workspace.py fix-ignores`）。

与 Vault 知识条目分离：组目录不进 `answers|decisions|references` 共享链路。
