# Chat UI 会话列表修复说明

## 问题根源

Chat UI 使用了**两套不同的存储系统**，导致数据不一致：

### 存储系统对比

| 功能 | 存储位置 | 数据格式 |
|------|---------|---------|
| **会话列表** (`/api/chat/sessions`) | 文件系统 `~/.hermes/webui/sessions/*.json` | JSON 文件 |
| **聊天流式** (`/api/chat/stream`) | SQLite 数据库 `~/.hermes/state.db` | 数据库表 |

### 问题表现

- 数据库中有 **33 个 webui 会话**（有完整消息）
- 文件系统中 **没有会话文件**（或很少）
- Chat UI 左侧 **显示空列表**

## 修复方案

已修改 `hermes_cli/web_chat_api/session_adapter.py` 中的三个函数，使其从 SQLite 数据库读取而不是文件系统：

### 1. `list_sessions_handler()`
- **修改前**: 从 `~/.hermes/webui/sessions/*.json` 读取
- **修改后**: 从数据库读取 `source='webui'` 的会话
- **回退**: 如果数据库读取失败，自动回退到文件系统

### 2. `get_session_endpoint_handler()`
- **修改前**: 从文件系统读取会话详情
- **修改后**: 从数据库读取会话和消息
- **回退**: 如果数据库读取失败，自动回退到文件系统

### 3. `delete_session_handler()`
- **修改前**: 删除文件系统中的会话文件
- **修改后**: 从数据库删除会话
- **回退**: 如果数据库操作失败，自动回退到文件系统

## 如何应用修复

### 方法 1: 重启 Web 服务器（推荐）

```bash
# 停止当前的 dashboard 服务器（Ctrl+C）

# 重新启动
hermes dashboard --port 9119
```

### 方法 2: 如果服务器在后台运行

```bash
# 找到进程
ps aux | grep "hermes dashboard"

# 杀死进程
kill <PID>

# 重新启动
hermes dashboard --port 9119
```

## 验证修复

1. **打开浏览器访问**: http://localhost:9119/chat

2. **检查左侧边栏**:
   - 应该看到 "Chat" 页签
   - 点击后应该显示你的 33 个会话
   - 每个会话显示标题、消息数、时间

3. **测试功能**:
   - 点击会话应该能加载历史消息
   - 删除会话应该能正常工作
   - 搜索功能应该能搜索消息内容

## 预期结果

修复后，Chat UI 应该显示：

```
Chat (33)
├── chat-98c3b983b392 (2 messages) - 今天
├── chat-58eb10c4ed3d (58 messages) - 今天
├── chat-21e4e65a17e9 (8 messages) - 今天
├── chat-671a2545913f (2 messages) - 昨天
└── ... (更多会话)
```

## 技术细节

### 数据库查询

修复后的代码使用以下查询获取会话：

```python
db.list_sessions_rich(
    source="webui",      # 只获取 webui 会话
    limit=100,           # 最多 100 个
    offset=0,            # 从第一个开始
    include_children=False  # 不包括子会话
)
```

### 数据格式转换

数据库格式 → Chat UI 格式：

```python
{
    "session_id": s["id"],
    "title": s.get("title") or "Untitled",
    "created_at": s.get("started_at", 0),
    "updated_at": s.get("last_active", 0),
    "message_count": s.get("message_count", 0),
    "model": s.get("model", ""),
    "workspace": "",
    "is_cli_session": False,
}
```

## 回退机制

如果数据库读取失败（例如数据库损坏），代码会自动回退到原来的文件系统实现，确保不会完全破坏功能。

## 常见问题

### Q: 修复后还是看不到会话？
A: 
1. 确认已重启 web 服务器
2. 清除浏览器缓存（Ctrl+Shift+R）
3. 检查浏览器控制台是否有错误

### Q: 会话数量不对？
A: 修复只显示 `source='webui'` 的会话。CLI 会话不会显示在 Chat UI 中。

### Q: 旧的文件系统会话怎么办？
A: 如果 `~/.hermes/webui/sessions/` 中有旧会话文件，它们不会自动迁移。可以手动删除或保留作为备份。

### Q: 会影响其他功能吗？
A: 不会。修复只影响 Chat UI 的会话列表。Dashboard 的 `/sessions` 页面不受影响。

## 后续改进建议

1. **统一存储**: 将所有会话存储统一到数据库
2. **迁移工具**: 提供工具将文件系统会话迁移到数据库
3. **配置选项**: 允许用户选择存储后端（文件 vs 数据库）

## 相关文件

- `hermes_cli/web_chat_api/session_adapter.py` - 会话适配器（已修改）
- `hermes_cli/web_server.py` - Web 服务器路由
- `hermes_cli/web_chat_dist/sessions.js` - 前端会话列表渲染
- `hermes_state.py` - SQLite 数据库接口

## 测试

运行测试脚本验证修复：

```bash
python3 test_chat_ui_fix.py
```

应该看到：
```
✓ PASS: list_sessions
✓ PASS: get_session
✓ All tests passed!
```
