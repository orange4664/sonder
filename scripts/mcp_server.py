#!/usr/bin/env python3
"""Optional local stdio MCP companion for one existing Sonder revision workspace."""
import argparse
from functools import partial
import importlib.util
from pathlib import Path
import sqlite3
import sys
from typing import Any

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def create_server(workspace):
    try:
        import anyio
        from mcp.server import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.types import ToolAnnotations
    except ImportError as error:
        raise ValueError('optional MCP support requires the SDK; install with this Python: '
                         f'-m pip install -r "{ROOT / "scripts/requirements-mcp.txt"}"') from error
    spec = importlib.util.spec_from_file_location('sonder_workspace_tools', ROOT / 'library/mcp_tools.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tools = module.WorkspaceTools(workspace)
    server = MCPServer('Sonder', version='1', log_level='WARNING', instructions=(
        'Use sonder_status first and verify this server is bound to the intended revision workspace. '
        'Keep the author in control: rank never approves a sentence. Record only an explicit saved '
        'author choice with its original round_id; retry that same decision after uncertain results. '
        'Preserve the saved learn_from_choice:false flag when the author excludes learning. '
        'These tools do not edit manuscript text.'))

    async def invoke(method, *args):
        try:
            return await anyio.to_thread.run_sync(partial(method, *args))
        except (ValueError, OSError, sqlite3.Error) as error:
            raise ToolError(str(error)) from error

    reading = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    store_read = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
    # An explicitly configured legacy embed matcher retains its model-download
    # behavior. The default ngram matcher and local Stanza syntax remain offline.
    nearest_hint = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)

    @server.tool(name='sonder_status', annotations=reading, structured_output=True)
    async def status() -> dict[str, Any]:
        """Inspect bound workspace, instance and engine capabilities without opening the database or models."""
        return await invoke(tools.status)

    @server.tool(name='sonder_learning_policy', annotations=store_read, structured_output=True)
    async def learning_policy(action: str | None = None) -> dict[str, Any]:
        """Inspect or configure this workspace's learning policy. Use ensure-adaptive on setup/resume to enable adaptive-v3 only if unset, preserving every saved policy. legacy/adaptive-v1/adaptive-v2/adaptive-v3 select explicitly; switching correction policies clears only the correction trace, preserving shared learned weights. May initialize/upgrade and snapshot legacy weights once; never records an author choice."""
        return await invoke(tools.learning_policy, action)

    @server.tool(name='sonder_bind', annotations=store_read, structured_output=True)
    async def bind(draft_file: str, output_file: str) -> dict[str, Any]:
        """Bind a saved literal-fragment draft to candidate-inputs JSON; never overwrite a different file."""
        return await invoke(tools.bind, draft_file, output_file)

    @server.tool(name='sonder_prepare', annotations=store_read, structured_output=True)
    async def prepare(section: str, role: str, draft_file: str, output_file: str,
                      summary: bool = False) -> dict[str, Any]:
        """Bind and publish this draft, then rank the same candidates. Stop on binding failure; never rank a stale output. Ranking failure may leave the valid canonical for retry. Never approves or learns; may initialize/upgrade the store."""
        return await invoke(tools.prepare, section, role, draft_file, output_file, summary)

    @server.tool(name='sonder_nearest', annotations=nearest_hint, structured_output=True)
    async def nearest(sentence_file: str, limit: int = 3, text_format: str | None = None) -> dict[str, Any]:
        """Retrieve raw voice exemplars; optional markdown interprets the query file's format. May initialize/upgrade the store."""
        return await invoke(tools.nearest, sentence_file, limit, text_format)

    @server.tool(name='sonder_rank', annotations=store_read, structured_output=True)
    async def rank(section: str, role: str, candidate_file: str, summary: bool = False) -> dict[str, Any]:
        """Rank saved candidates; summary omits repeated annotations, preserving text, scores and diagnostics. Never approves or learns. May initialize/upgrade the store."""
        return await invoke(tools.rank, section, role, candidate_file, summary)

    @server.tool(name='sonder_record_choice', annotations=store_read, structured_output=True)
    async def record_choice(section: str, role: str, candidate_file: str, round_id: str,
                            receipt_file: str | None = None) -> dict[str, Any]:
        """Finalize an explicitly approved saved choice, honoring learn_from_choice:false. Require its saved round_id; optionally export the original receipt inside choice-receipts. If export fails after feedback, retry the original file and ID."""
        return await invoke(tools.record_choice, section, role, candidate_file, round_id, receipt_file)

    @server.tool(name='sonder_prune', annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False), structured_output=True)
    async def prune() -> dict[str, Any]:
        """At the paragraph checkpoint, clean stale compatibility counters/exemplars. Adaptive model and snapshot rows remain and decay on read/update."""
        return await invoke(tools.prune)

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path,
                        help='existing paper-stem-revision directory; one workspace per server')
    args = parser.parse_args()
    try:
        server = create_server(args.workspace)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Sonder MCP could not start: {error}\n')
    server.run(transport='stdio')


if __name__ == '__main__':
    main()
