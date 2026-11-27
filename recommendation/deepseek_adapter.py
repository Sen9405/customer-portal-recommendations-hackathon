"""Server-only DeepSeek adapter for fixed public research scenarios."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

URL = 'https://api.deepseek.com/chat/completions'
MODEL = 'deepseek-flash'


class DeepSeekError(RuntimeError):
    def __init__(self, status_code):
        super().__init__('DeepSeek request failed')
        self.status_code = status_code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@contextmanager
def client():
    key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not key and (directory := os.environ.get('CREDENTIALS_DIRECTORY')):
        key = (Path(directory) / 'deepseek.key').read_text(encoding='ascii')
    if not key or not key.isascii() or any(not 33 <= ord(c) <= 126 for c in key):
        raise RuntimeError('DeepSeek credential missing or invalid')
    yield build_opener(ProxyHandler({}), NoRedirect()), key


def invoke(connection, instruction, payload):
    opener, key = connection
    data = json.dumps({'model': MODEL, 'messages': [
        {'role': 'system', 'content': instruction + '\nReturn exactly one JSON object.'},
        {'role': 'user', 'content': payload},
    ], 'thinking': {'type': 'disabled'}, 'response_format': {'type': 'json_object'},
        'temperature': 0, 'max_tokens': 128, 'stream': False}, ensure_ascii=False).encode()
    request = Request(URL, data=data, headers={'Authorization': 'Bearer ' + key,
                                               'Content-Type': 'application/json'})
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(262145)
    except HTTPError as error:
        status = error.code
        error.close()
        raise DeepSeekError(status) from None
    if len(body) > 262144:
        raise RuntimeError('DeepSeek response too large')
    result = json.loads(body)
    choice, = result['choices']
    message = choice['message']
    content = message.get('content')
    if choice['finish_reason'] != 'stop' or message.get('tool_calls') or not isinstance(content, str) or not content.strip():
        raise RuntimeError('Incomplete DeepSeek response')
    return content, result.get('usage')
