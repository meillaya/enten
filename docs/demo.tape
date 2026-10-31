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
Show

Type "uv run --project packages/python/enten enten review --repo . --verify-command 'python -m compileall -q .' --store-path /tmp/enten-demo-reviews.json"
Enter
Sleep 5s

Type "make eval"
Enter
Sleep 4s

Type "uv run --project packages/python/enten enten cost --store-path /tmp/enten-demo-reviews.json"
Enter
Sleep 3s

Type "uv run --project packages/python/enten enten reconcile --help"
Enter
Sleep 3s
