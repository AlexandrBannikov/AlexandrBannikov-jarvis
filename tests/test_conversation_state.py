from app.conversation import ConversationManager, ConversationStorage, PendingQuestion


def test_pending_short_answer_persists_and_is_owner_scoped(tmp_path):
    storage = ConversationStorage(tmp_path / "conversation.db")
    manager = ConversationManager(storage)
    key = manager.key(10, 20)
    manager.record_assistant(
        key, "Уточните двигатель и мощность?",
        pending=PendingQuestion("engine_spec", "Уточните двигатель и мощность?", ["engine_displacement", "engine_power"]),
    )
    assert manager.record_user(key, "1.4 л, 150 л.с.") == "ANSWER_TO_PENDING"
    state = storage.get_state(key)
    assert state is not None and state.pending_question is None
    assert state.collected_facts["engine_displacement"] == "1.4 л"
    assert storage.get_state(manager.key(11, 20)) is None


def test_history_is_bounded_and_has_provenance(tmp_path):
    storage = ConversationStorage(tmp_path / "conversation.db", max_messages=4)
    manager = ConversationManager(storage)
    key = manager.key(1, 2)
    for index in range(8):
        storage.append_message(key, "user", str(index))
    messages = storage.recent_messages(key)
    assert [m["content"] for m in messages] == ["4", "5", "6", "7"]
    assert all(m["provenance"] == "RECENT_HISTORY" for m in messages)


def test_cancel_and_new_topic_do_not_guess_persistent_project(tmp_path):
    storage = ConversationStorage(tmp_path / "conversation.db")
    manager = ConversationManager(storage)
    key = manager.key(1, 2)
    manager.record_assistant(key, "Какой год?", pending=PendingQuestion("year", "Какой год?", ["year"]))
    assert manager.record_user(key, "А какая завтра погода?") == "NEW_TOPIC"
    state = storage.get_state(key)
    assert state is not None and state.pending_question is None and state.active_topic == ""
