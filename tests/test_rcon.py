from armactl.rcon import PlayerEntry, _parse_player_lines


def test_parse_player_lines_ignores_battleye_noise():
    response = """
Logged In! Client ID: #0
Processing Command: #players
; 0109fcf5-a861-4002-881e-8a497c59797c ; MisanTropiC#DivisioN (#1)
""".strip()

    entries = _parse_player_lines(response)

    assert len(entries) == 1
    assert entries[0].name == "MisanTropiC#DivisioN"
    assert entries[0].player_id == "1"
    assert entries[0].guid == "0109fcf5-a861-4002-881e-8a497c59797c"


def test_parse_player_lines_supports_legacy_numeric_format():
    response = "17 Denis"

    entries = _parse_player_lines(response)

    assert entries == [
        PlayerEntry(name="Denis", player_id="17", raw="17 Denis")
    ]


def test_parse_player_lines_ignores_players_header_lines():
    response = """
Players on server:
Players: 1
""".strip()

    entries = _parse_player_lines(response)

    assert entries == []


def test_parse_player_lines_keeps_unknown_nonempty_lines_as_fallback():
    response = "Some Unexpected Line"

    entries = _parse_player_lines(response)

    assert len(entries) == 1
    assert entries[0].name == "Some Unexpected Line"
    assert entries[0].player_id is None
    assert entries[0].guid is None
