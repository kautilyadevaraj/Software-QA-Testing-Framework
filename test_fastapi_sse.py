import asyncio
from fastapi import FastAPI, Depends, Request, Response
from sse_starlette.sse import EventSourceResponse
import uvicorn

app = FastAPI()

def get_current_user(response: Response):
    response.set_cookie(key="my_cookie", value="my_value")
    return "user"

@app.get("/stream")
async def stream(response: Response, user: str = Depends(get_current_user)):
    async def event_generator():
        yield {"data": "hello"}
    
    es_response = EventSourceResponse(event_generator())
    es_response.raw_headers.extend(response.raw_headers)
    return es_response

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8123)
