# 运行与连接

## Windows本地运行

安装Python 3.10+，在仓库`panel`目录运行`./start.ps1`。脚本创建`.venv`并安装依赖，打开 http://127.0.0.1:8765/ 。不需要全局安装FastAPI。端口占用时先检查已有服务，不要盲目关闭其他程序。

其他系统可创建venv、安装`panel/requirements.txt`，在panel目录运行`python -m uvicorn app:app --host 127.0.0.1 --port 8765`。界面保存凭据使用Windows DPAPI，其他系统改用服务端环境变量`DIFY_API_KEY`。

## Dify

导入`workflow/researchlab-v6.yml`。这是已测试版本的脱敏导出，去除了工作区图标URL与凭据ID；具体导入兼容性受Dify版本影响，尚未在新的空白账号完成复现。

1. 安装导出所需的模型提供商和Tavily插件。
2. 在自己的账号配置模型及搜索工具。替换所有LLM节点模型，检查思考输出设置与上下文能力；不要只改第一个节点。
3. 先在Dify编辑器用公开问题测试，确认输入变量为`question/materials/use_case/candidates/deployment/must_haves`。
4. 发布应用，取得自己的应用API Key，在本地面板设置中保存。模型API Key与Dify应用API Key不是同一个。
5. 默认服务基址为`https://api.dify.ai/v1`；自建环境可通过服务端`DIFY_API_BASE`设置。

保存任务不调用模型，发起执行会消耗你配置的模型/搜索额度。没有永久免费的承诺。

## 数据与凭据

SQLite在panel/data；Windows凭据在当前用户本机加密目录，不随源码分发。服务只监听localhost，不带生产认证。公开部署前需重做用户授权、存储隔离和调用配额；不能直接把开发服务暴露外网。异常断流不自动重试，重启标记interrupted；先查Dify记录再决定是否重跑，避免重复调用。

## 公开样本

examples是从真实运行中缩减的回放，保留真实耗时/tokens/状态、来源链接和复核问题，省略网页正文、完整事实、账号与运行ID。不提供原始数据库，且这些样本不能用于计算语义准确率。界面显示0条事实时可能是公开样本主动省略，不代表原始运行没有事实。
