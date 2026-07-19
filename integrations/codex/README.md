# Codex 接入

Codex 在本仓库中遵循根目录 `AGENTS.md`，可直接使用 `robotctl` 或 `stackchan-family-admin` MCP：

```bash
.venv/bin/robotctl character show
.venv/bin/robotctl character style "回答简短；给儿童解释时一次只讲一个概念。"
.venv/bin/robotctl tasks report --id codex-example --source codex --title "更新角色" --status completed --progress 1
```

关键约束：

- 先读取活动版本，再提交带 `base_version` 的变更。
- 不直接修改运行数据库；所有角色改变都经过版本 API。
- 不跨用户读取或复制记忆。
- 不把 OpenAI 密钥、脸部特征或对话数据写入 Git 或固件。
- 影响儿童安全和家庭设备权限的变更，需要成人审阅后发布。
