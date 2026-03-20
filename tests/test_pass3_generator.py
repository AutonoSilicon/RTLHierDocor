from agent.pass3_generator import Pass3Generator


def test_pass3_3_1_resume_stays_enabled():
    assert Pass3Generator._pass3_3_1_resume_enabled() is True


def test_pass3_3_2_resume_is_disabled_to_force_rerun():
    assert Pass3Generator._pass3_3_2_resume_enabled() is False
