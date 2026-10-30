"""`genesis runner` — execute your Genesis coding-agent tasks locally.

Connect this machine to a Genesis server with a runner token and your builds'
agent tasks run HERE, through your own local `claude` CLI (your subscription)
— while the server keeps all orchestration, validation, repair policy,
commits, gates, evidence, and billing.

Public package rule: this code never imports genesis_server or
genesis_executor; the wire protocol (claim / workspace / events / result)
is its only contract with the server.
"""
