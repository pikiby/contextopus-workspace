"""Run the bundled long-lived MCP process with an explicitly configured interpreter."""
import argparse


def main():
    parser = argparse.ArgumentParser(description="PCW local memory MCP runtime")
    parser.add_argument("transport", nargs="?", choices=["stdio"], default="stdio")
    parser.parse_args()
    from .server import mcp
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
