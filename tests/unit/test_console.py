from iructl._console import OutputConsole


def test_long_str(capsys):
    console = OutputConsole()
    long_string = "a" * 1000
    console.print(long_string)
    captured = capsys.readouterr()
    assert long_string == "".join(captured.out.splitlines())
    assert len(captured.out) > 0


def test_print_with_leading_blank(capsys):
    console = OutputConsole()
    console.print_with_leading_blank("hello")
    captured = capsys.readouterr()
    assert captured.out.startswith("\n")
    assert captured.out.splitlines() == ["", "hello"]
