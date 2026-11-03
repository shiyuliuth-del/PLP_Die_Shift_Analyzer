# 贡献入口

请先阅读 [AGENTS.md](AGENTS.md) 和 [Agent.md](Agent.md)。人类维护者与自动Agent共用同一份设计与提交规范。

- `main`是唯一长期主线；在短期任务分支修改并通过PR集成。
- 每个任务先同步主线，避免共享工作目录和覆盖其他贡献者的修改。
- PR按[模板](.github/pull_request_template.md)说明实际行为、验证范围和遗留项。
- 当前模型、输入口径和已废弃功能见[设计要求](docs/DESIGN_REQUIREMENTS.md)。
- 本地最低入口：`python3 tools/check_contribution.py`，然后运行与改动相称的核心及GUI测试；完整命令见Agent.md。
- 合并必须依据用户/维护者授权；合并后删除自己的任务分支，保留历史tag。
