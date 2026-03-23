import asyncio

from agent.llm_backend import LLMBackend


class _FakeDelta:
    def __init__(self, reasoning_content=None, content=None, tool_calls=None):
        self.reasoning_content = reasoning_content
        self.content = content
        self.tool_calls = tool_calls


class _FakeToolFunction:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _FakeToolCallDelta:
    def __init__(self, index=0, id=None, type="function", function=None):
        self.index = index
        self.id = id
        self.type = type
        self.function = function


class _FakeChoice:
    def __init__(self, delta=None, message=None):
        self.delta = delta
        self.message = message


class _FakeChunk:
    def __init__(self, choices=None, usage=None):
        self.choices = choices or []
        self.usage = usage


class _FakeUsage:
    def __init__(self, prompt_tokens=0, completion_tokens=0, total_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class _FakeAsyncStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def test_extract_thinking_content_strips_think_tags():
    cleaned, thinking = LLMBackend._extract_thinking_content(
        "<think>step1\nstep2</think>\n```json\n{\"status\":\"complete\"}\n```"
    )

    assert thinking == "step1\nstep2"
    assert cleaned == "```json\n{\"status\":\"complete\"}\n```"


def test_consume_streaming_chat_completion_collects_reasoning_and_usage():
    backend = object.__new__(LLMBackend)
    aggregated = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    stream = _FakeAsyncStream(
        [
            _FakeChunk([_FakeChoice(delta=_FakeDelta(reasoning_content="think-1 "))]),
            _FakeChunk([_FakeChoice(delta=_FakeDelta(reasoning_content="think-2", content="```json\n"))]),
            _FakeChunk([_FakeChoice(delta=_FakeDelta(content="{\"status\":\"complete\"}\n```"))]),
            _FakeChunk([], _FakeUsage(prompt_tokens=12, completion_tokens=5, total_tokens=17)),
        ]
    )

    message, reasoning = asyncio.run(backend._consume_streaming_chat_completion(stream, aggregated))

    assert reasoning == "think-1 think-2"
    assert message.content == "```json\n{\"status\":\"complete\"}\n```"
    assert aggregated == {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17}


def test_consume_streaming_chat_completion_collects_streamed_tool_calls():
    backend = object.__new__(LLMBackend)
    aggregated = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    stream = _FakeAsyncStream(
        [
            _FakeChunk(
                [
                    _FakeChoice(
                        delta=_FakeDelta(
                            reasoning_content="thinking",
                            tool_calls=[
                                _FakeToolCallDelta(
                                    index=0,
                                    id="call_1",
                                    function=_FakeToolFunction(name="grepSource", arguments="{\"pattern\":\""),
                                )
                            ],
                        )
                    )
                ]
            ),
            _FakeChunk(
                [
                    _FakeChoice(
                        delta=_FakeDelta(
                            tool_calls=[
                                _FakeToolCallDelta(
                                    index=0,
                                    function=_FakeToolFunction(arguments="pcgen_ifctrl_pc\"}"),
                                )
                            ],
                        )
                    )
                ]
            ),
            _FakeChunk([], _FakeUsage(prompt_tokens=9, completion_tokens=4, total_tokens=13)),
        ]
    )

    message, reasoning = asyncio.run(backend._consume_streaming_chat_completion(stream, aggregated))

    assert reasoning == "thinking"
    assert message.content == ""
    assert len(message.tool_calls) == 1
    assert message.tool_calls[0].id == "call_1"
    assert message.tool_calls[0].function.name == "grepSource"
    assert message.tool_calls[0].function.arguments == "{\"pattern\":\"pcgen_ifctrl_pc\"}"
    assert aggregated == {"input_tokens": 9, "output_tokens": 4, "total_tokens": 13}
