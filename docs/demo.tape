Output "docs/demo.gif"

Set Shell "bash"
Set FontFamily "Menlo"
Set FontSize 15
Set Width 1100
Set Height 620
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set CursorBlink false

Hide
Type "clear"
Enter
Type "rm -rf .enten"
Enter
Show

Type "uv run --project packages/python/enten enten"
Enter
Sleep 5s

Type "uv run --project packages/python/enten enten --json"
Enter
Sleep 3s

Type "uv run --project packages/python/enten enten --help"
Enter
Sleep 3s
