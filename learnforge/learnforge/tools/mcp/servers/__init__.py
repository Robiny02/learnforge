"""Built-in stdio MCP servers for LearnForge.

These modules are launched as subprocesses through the same MCP client used for
external servers. They intentionally avoid the official MCP SDK so the app keeps
working on the project's lowest supported Python runtime.
"""

