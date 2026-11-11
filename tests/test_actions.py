from secretary.actions import Action, ActionFilter, strip_actions


def run(text: str, size: int = 3) -> tuple[str, list[Action]]:
    f = ActionFilter()
    out = "".join(f.feed(text[i : i + size]) for i in range(0, len(text), size)) + f.flush()
    return out, f.actions


def test_actions_are_pulled_out_of_the_stream():
    out, actions = run("[happy] Done. [note: vault code 5526] Anything else? [cross off: vault]")
    assert out == "[happy] Done. Anything else? "
    assert actions == [Action("write", "vault code 5526"), Action("cross_off", "vault")]


def test_open_page_and_spelling_variants():
    _, actions = run("[Open Page: Escape room] [CrossOff: gate] [write: x]")
    assert actions == [
        Action("open_page", "Escape room"),
        Action("cross_off", "gate"),
        Action("write", "x"),
    ]


def test_other_brackets_are_left_alone():
    assert run("I said [sic] it, see [1] and [note to self")[0] == (
        "I said [sic] it, see [1] and [note to self"
    )


def test_strip_actions():
    assert strip_actions("Done. [note: a] ok") == "Done. ok"


def test_rename_tag():
    assert run("Done. [rename page: Haunted House]")[1] == [Action("rename_page", "Haunted House")]


def test_claims():
    from secretary.actions import claims_an_action

    assert claims_an_action("I have crossed off the gate code 3258 for you.")
    assert claims_an_action("Okay, I've written that down.")
    assert claims_an_action("I've got that written down for you!")
    assert claims_an_action("Done, crossed it off.")
    assert not claims_an_action("The gate code is 3258.")
    assert not claims_an_action("I can do that for you.")
