# Chat UI "Connection lost" Error - Root Cause Analysis

## Problem
When sending a message in the Chat UI, the frontend receives a "Connection lost" error. The server logs show:
```
GET /api/session?session_id=chat-b16ae6ca6fae HTTP/1.1" 404 Not Found
```

## Root Cause

The issue is in the session persistence flow:

### Current Flow:
1. **Frontend** sends message → `/api/chat/start`
2. **web_server.py** creates in-memory session in `_chat_sessions` dict
3. **chat_stream.py** creates AIAgent with `platform='webui'` and `session_db`
4. **AIAgent.__init__** calls `session_db.create_session(session_id, source='webui', ...)`
5. **Frontend** polls `/api/session?session_id=...` to get session details
6. **session_adapter.py** `get_session_endpoint_handler()` queries database
7. **Database query** checks `if session_meta.get("source") != "webui": return None`
8. **Result**: 404 error because session not found or source mismatch

### The Problem:
The session IS being created in the database by AIAgent, but there's a timing or data issue:
- Either the session isn't being created fast enough (race condition)
- Or the session is created but with wrong source value
- Or the session query is failing for another reason

## Investigation Steps

1. **Check if session is created in database**
   - Add logging to see if `AIAgent.__init__` successfully creates the session
   - Check if `session_db.create_session()` is being called with correct parameters

2. **Check timing**
   - The frontend might be polling `/api/session` before AIAgent creates it
   - Need to ensure session exists before returning from `/api/chat/start`

3. **Check source field**
   - Verify that `platform='webui'` is correctly passed to AIAgent
   - Verify that `source='webui'` is set in the database

## Proposed Fix

### Option 1: Pre-create session in database (RECOMMENDED)
Before starting the chat stream, explicitly create the session in the database:

```python
# In /api/chat/start endpoint, before calling run_chat_stream:
from hermes_state import SessionDB
db = SessionDB()
try:
    # Pre-create session with source='webui'
    db.create_session(
        session_id=session_id,
        source='webui',
        model=session["model"],
    )
except Exception as e:
    logger.warning(f"Failed to pre-create session: {e}")
finally:
    db.close()
```

### Option 2: Fix session_adapter query
Modify `get_session_endpoint_handler()` to be more lenient:
- Don't filter by source if session doesn't exist
- Or create session on-demand if not found

### Option 3: Synchronize session creation
Ensure AIAgent creates session before `/api/chat/start` returns:
- Wait for AIAgent initialization to complete
- Add a callback to confirm session creation

## Next Steps

1. Add debug logging to track session creation
2. Implement Option 1 (pre-create session)
3. Test the fix
4. Verify session persistence after chat completion
