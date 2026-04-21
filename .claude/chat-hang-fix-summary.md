# 聊天页面挂起问题修复总结

**修复日期**: 2026-04-21
**问题**: http://127.0.0.1:9119/chat 页面在执行脚本命令时长时间卡住无响应

---

## 修复的关键问题

### ✅ CRITICAL #1: 阻塞事件循环 (已修复)

**文件**: `hermes_cli/web_chat_api/chat_stream.py`

**问题**: 
- `run_chat_stream()` 异步函数使用阻塞的 `thread.join()` 调用
- 在等待 agent 执行时阻塞了整个事件循环
- 导致前端 SSE 连接冻结，无法接收任何更新

**修复方案**:
```python
# 修复前：使用阻塞的线程 join
agent_thread = threading.Thread(target=_run_agent, daemon=True)
agent_thread.start()
while agent_thread.is_alive():
    agent_thread.join(timeout=0.1)  # ❌ 阻塞事件循环

# 修复后：使用 run_in_executor 不阻塞事件循环
loop = asyncio.get_event_loop()
future = loop.run_in_executor(None, _run_agent)

while not future.done():
    if cancel_event.is_set():
        # 处理取消
        await asyncio.wait_for(asyncio.shield(future), timeout=5.0)
        return
    await asyncio.sleep(0.1)  # ✅ 不阻塞事件循环

status, result = await future
```

**改进**:
- 使用 `loop.run_in_executor()` 在线程池中运行同步代码
- 使用 `await asyncio.sleep()` 替代阻塞的 `thread.join()`
- 事件循环保持响应，可以处理其他异步任务
- 添加了执行超时机制（默认 10 分钟，可通过 `HERMES_CHAT_TIMEOUT` 环境变量配置）

---

### ✅ CRITICAL #2: 嵌套事件循环 (已修复)

**文件**: `hermes_cli/web_server.py`

**问题**:
- 在 `chat_stream_post()` 中使用 `asyncio.run()` 创建嵌套事件循环
- 在已有事件循环中创建新的事件循环导致复杂的线程问题
- 可能导致死锁和不可预测的行为

**修复方案**:
```python
# 修复前：创建嵌套事件循环
with concurrent.futures.ThreadPoolExecutor() as executor:
    future = loop.run_in_executor(
        executor,
        lambda: asyncio.run(run_chat_stream(...))  # ❌ 嵌套事件循环
    )
    while not future.done():
        await asyncio.sleep(0.05)

# 修复后：直接在当前事件循环中运行
chat_task = asyncio.create_task(run_chat_stream(
    session_id=session_id,
    user_message=body.message,
    model=session["model"],
    workspace=session["workspace"],
    on_token=on_token,
    on_tool=on_tool,
    on_complete=on_complete,
    on_error=on_error,
))

while not chat_task.done():
    while queue_data:
        event_type, data = queue_data.pop(0)
        yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    await asyncio.sleep(0.05)

await chat_task  # ✅ 在同一事件循环中等待
```

**改进**:
- 移除了 `ThreadPoolExecutor` 和嵌套的 `asyncio.run()`
- 使用 `asyncio.create_task()` 在当前事件循环中创建任务
- 简化了代码结构，减少了复杂性
- 避免了潜在的死锁和竞态条件

---

## 附加改进

### 1. 执行超时保护

添加了可配置的超时机制：
- 默认超时：600 秒（10 分钟）
- 环境变量：`HERMES_CHAT_TIMEOUT`
- 超时后自动中断 agent 执行并返回错误

### 2. 更好的取消处理

改进了取消逻辑：
- 使用 `asyncio.shield()` 保护取消操作
- 给予 agent 5 秒时间优雅关闭
- 避免强制终止导致的资源泄漏

### 3. 改进的错误处理

- 在多个层级添加了异常捕获
- 记录详细的错误日志
- 向前端返回有意义的错误消息

---

## 测试建议

### 1. 基本功能测试
```bash
# 启动服务器
python -m hermes_cli.main webui

# 访问 http://127.0.0.1:9119/chat
# 测试普通对话（不执行脚本）
```

### 2. 脚本执行测试
```bash
# 在聊天界面输入需要执行脚本的命令，例如：
"请列出当前目录的文件"
"运行 ls -la 命令"
"执行 python --version"

# 验证：
# - 页面不应卡住
# - 应该能看到实时的 token 流
# - 应该能看到工具调用进度
# - 脚本执行完成后应该返回结果
```

### 3. 长时间运行测试
```bash
# 测试长时间运行的命令
"运行 sleep 30 命令"

# 验证：
# - 页面保持响应
# - 可以看到进度更新
# - 30 秒后正常完成
```

### 4. 取消测试
```bash
# 开始一个长时间运行的任务
"运行 sleep 60 命令"

# 在执行过程中点击取消按钮

# 验证：
# - 任务应该被取消
# - 页面应该恢复正常
# - 不应该有资源泄漏
```

### 5. 超时测试
```bash
# 设置短超时用于测试
export HERMES_CHAT_TIMEOUT=10

# 运行超过 10 秒的命令
"运行 sleep 20 命令"

# 验证：
# - 10 秒后应该超时
# - 返回超时错误消息
# - 页面恢复正常
```

---

## 性能影响

### 改进前
- 事件循环被阻塞，无法处理其他请求
- 前端连接冻结，无法接收更新
- 用户体验差，看起来像是崩溃

### 改进后
- 事件循环保持响应
- 可以同时处理多个聊天会话
- 实时流式传输 token 和工具调用
- 用户可以看到执行进度

---

## 向后兼容性

✅ 完全向后兼容
- API 接口未改变
- 前端代码无需修改
- 现有会话继续工作
- 配置文件无需更新

---

## 环境变量

新增环境变量：

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `HERMES_CHAT_TIMEOUT` | `600` | 聊天执行超时时间（秒） |

---

## 相关文件

修改的文件：
1. `hermes_cli/web_chat_api/chat_stream.py` - 修复阻塞事件循环
2. `hermes_cli/web_server.py` - 修复嵌套事件循环

文档：
1. `.claude/code-review-chat-hang.md` - 原始问题分析
2. `.claude/chat-hang-fix-summary.md` - 本修复总结

---

## 后续建议

### 短期
1. 进行全面的集成测试
2. 监控生产环境中的性能指标
3. 收集用户反馈

### 中期
1. 考虑添加进度条显示长时间运行的任务
2. 实现更细粒度的取消控制
3. 添加任务队列支持多个并发请求

### 长期
1. 考虑使用 WebSocket 替代 SSE 以获得双向通信
2. 实现分布式任务执行支持
3. 添加任务持久化和恢复机制
