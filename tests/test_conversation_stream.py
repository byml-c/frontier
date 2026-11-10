from frontier.services.conversation_stream import ConversationStream


def test_detached_subscriber_never_buffers_more_events():
    stream = ConversationStream()
    stream.publish("before")
    stream.detach()
    for _ in range(1000):
        stream.publish("after")
    assert stream._queue.empty()


def test_slow_client_does_not_block_agent_or_accumulate_memory():
    stream = ConversationStream()
    for _ in range(1000):
        stream.publish("event")
    assert stream._queue.empty()
    assert '"type": "error"' in next(stream.events())


def test_finished_stream_drains_in_order():
    stream = ConversationStream()
    stream.publish("one")
    stream.publish("two")
    stream.finish()
    assert list(stream.events()) == ["one", "two"]
