import pytest

import main


def test_main_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        main.main(["--help"])

    assert exc.value.code == 0
