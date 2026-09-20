# 故障排查

| 现象 | 处理 |
|---|---|
| pip ERROR_OPEN_FAILED | 公司安全软件拦 exe；改 TEMP 或加白，勿死循环重试 |
| `server` 为 `sqlite:...` 仍想写 Redis | R4 BLOCKER：换带 pymysql 的解释器，配好 secrets |
| Redis 连上但无 Stream | 正常（3.2.12）；用 LIST 后端 |
| 无 amrd_qa_vault | 等 DBA X-4/X-5；本地 Vault 与 brief 可先用 |
| 删除文件 scan 崩溃 | 须用带 `-M` 与 D 类型分支的 vault_scan（方案已修） |
| 凭据进 SVN | 检查忽略；`vault_admin.py check` |

回滚：SVN/Git revert skill 代码不影响已入库知识；共享侧用 supersede 软删。
