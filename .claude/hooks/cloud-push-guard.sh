#!/bin/bash
# PreToolUse(Bash) hook: in Claude Code cloud sessions, block pushes to the
# long-lived branches, pull request merges, releases, deploys and workflow
# dispatches.
#
# Cloud sessions reach GitHub through Anthropic's proxy with the repo owner's
# credentials. The proxy does not limit which branch a push updates, and branch
# protection lets admins bypass it, so a cloud session could otherwise push
# straight to develop/main (and marketing/ops deploy on push to develop).
# Local sessions are untouched: the hook exits unless CLAUDE_CODE_REMOTE=true.
#
# It reads the command text with shell quoting rules, so it catches mistakes,
# not a deliberate bypass (a variable holding "develop", --no-verify, ...).
# Threvo's copy is canonical and tested by scripts/tests/test_claude_cloud_hooks.py;
# the nested repositories carry verbatim copies.
#
# Exit 2 = block the command; exit 0 = allow.

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

# The hook input arrives on stdin; move it to fd 3 so python3 can read its
# program from the heredoc. python3 is part of the cloud image; jq is not.
exec python3 - 3<&0 <<'PY'
import json
import os
import re
import shlex
import subprocess
import sys

PROTECTED = {"develop", "main", "staging"}
OWNER_ONLY = re.compile(
    r"^(deploy|deploy\.sh|release\.sh|(certify|dispatch|publish)-prod-.*|promote-.*)$"
)
WRAPPERS = {"sudo", "nohup", "time", "command", "exec", "nice", "then", "do", "else",
            "if", "elif", "while", "until", "!", "{", "}"}
SHELLS = {"bash", "sh", "zsh"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def block(reason: str) -> None:
    print(f"Blocked in cloud sessions: {reason}.", file=sys.stderr)
    print("Push a branch of your own (for example claude/<topic>) and open a PR "
          "to develop. Merges, releases and deploys stay with the owner.", file=sys.stderr)
    sys.exit(2)


def without_heredoc_bodies(script: str) -> str:
    # Heredoc bodies are data (commit messages, PR bodies), not commands.
    kept, end = [], None
    for line in script.split("\n"):
        if end is not None:
            if line.strip() == end:
                end = None
            continue
        kept.append(line)
        for match in re.finditer(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][\w-]*)\1", line):
            # `-m "a <<EOF b"` is text; `--body "$(cat <<'EOF'` opens a heredoc.
            before = line[:match.start()]
            if before.count("((") > before.count("))"):
                continue  # $((1 << n)) is a shift
            quote = open_quote(before)
            if not quote or quote not in line[match.end():]:
                end = match.group(2)
                break
    return "\n".join(kept)


def open_quote(text: str) -> str:
    quote, i = "", 0
    while i < len(text):
        char = text[i]
        if quote == "'":
            quote = "" if char == "'" else quote
        elif char == "\\":
            i += 1
        elif quote == '"':
            quote = "" if char == '"' else quote
        elif char in "'\"":
            quote = char
        i += 1
    return quote


def without_comments(script: str) -> str:
    # `#` starts a comment only at the start of a word and outside quotes.
    out, quote, i = [], "", 0
    while i < len(script):
        char = script[i]
        if quote:
            if char == quote:
                quote = ""
            elif char == "\\" and quote == '"':
                out.append(script[i:i + 2])
                i += 2
                continue
        elif char in "'\"":
            quote = char
        elif char == "\\":
            out.append(script[i:i + 2])
            i += 2
            continue
        elif char == "#" and (not out or out[-1][-1] in " \t\n;&|()"):
            while i < len(script) and script[i] != "\n":
                i += 1
            continue
        out.append(char)
        i += 1
    return "".join(out)


def tokens(script: str) -> list[str]:
    try:
        lexer = shlex.shlex(script, posix=True, punctuation_chars=";&|()<>\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:  # unbalanced quotes: read it without quoting rules
        return re.findall(r"[;&|()<>\n]+|[^\s;&|()<>]+", script.replace('"', " ").replace("'", " "))


def current_branch(git_dir: str) -> str:
    try:
        result = subprocess.run(["git", "-C", git_dir, "symbolic-ref", "--quiet", "--short", "HEAD"],
                                capture_output=True, text=True, timeout=5)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()


def has_ref(git_dir: str, *refs: str) -> bool:
    # `rev-parse --verify` checks exactly one ref per call.
    for ref in refs:
        try:
            result = subprocess.run(["git", "-C", git_dir, "rev-parse", "--verify", "--quiet", ref],
                                    capture_output=True, text=True, timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False
        if result.returncode == 0:
            return True
    return False


def resolve(base: str, path: str) -> str:
    return os.path.normpath(os.path.join(base, os.path.expanduser(path)))


def check_script(script: str, cwd: str, switched: dict[str, str]) -> None:
    dirs, words, redirect = [cwd], [], False
    # Join continued lines; `\\` before a newline is an escaped backslash.
    script = re.sub(r"(?<!\\)((?:\\\\)*)\\\n", r"\1 ", without_heredoc_bodies(script))
    for token in tokens(without_comments(script)) + ["\n"]:
        if redirect:
            redirect = False
            continue
        if not token or not set(token) <= set(";&|()<>\n"):
            words.append(token)
            continue
        if "<" in token or ">" in token:  # redirection: drop `2` in 2>&1 and the target
            if words and words[-1].isdigit():
                words.pop()
            redirect = not token.endswith("&") or token in ("&>", ">&")
            continue
        if words:
            check_command(words, dirs, switched)
            words = []
        for char in token:
            if char == "(":
                dirs.append(dirs[-1])
            elif char == ")" and len(dirs) > 1:
                dirs.pop()


def check_command(words: list[str], dirs: list[str], switched: dict[str, str]) -> None:
    i = 0
    while i < len(words):
        word = words[i]
        if word in WRAPPERS or ASSIGNMENT.match(word):
            i += 1
        elif word in ("env", "timeout"):
            i += 1
            while i < len(words) and (words[i].startswith("-") or ASSIGNMENT.match(words[i])):
                i += 1
            if word == "timeout":
                i += 1  # the duration
        else:
            break
    if i >= len(words):
        return
    entry, args = words[i], words[i + 1:]
    name = os.path.basename(entry)

    if entry in ("cd", "pushd"):
        if args and not args[0].startswith("-"):
            dirs[-1] = resolve(dirs[-1], args[0])
        return
    if OWNER_ONLY.match(name):
        block(f"owner-only script ({entry})")
    if entry == "eval":
        check_script(" ".join(args), dirs[-1], switched)
        return
    if name in SHELLS or entry in ("source", "."):
        k = 0
        while k < len(args):
            arg = args[k]
            if re.match(r"^-[a-z]*c[a-z]*$", arg):
                if k + 1 < len(args):
                    check_script(args[k + 1], dirs[-1], switched)
                return
            if not arg.startswith(("-", "+")):
                if OWNER_ONLY.match(os.path.basename(arg)):
                    block(f"owner-only script ({arg})")
                return
            k += 2 if re.match(r"^[-+][a-z]*o$", arg) else 1  # -o pipefail, -euo pipefail
        return
    if name == "gh":
        check_gh(args)
    elif name == "curl":
        check_curl(args)
    elif name == "git":
        check_git(args, dirs[-1], switched)


def adjacent(args: list[str], first: str, second: str) -> bool:
    return any(a == first and b == second for a, b in zip(args, args[1:]))


def check_gh(args: list[str]) -> None:
    if adjacent(args, "workflow", "run"):
        block("workflow dispatch")
    if adjacent(args, "pr", "merge"):
        block("pull request merge")
    if adjacent(args, "run", "rerun"):
        block("workflow re-run")
    # A v* tag deploys the frontend and Marketing to production.
    words = [a for k, a in enumerate(args) if not a.startswith("-") and (k == 0 or args[k - 1] not in ("-R", "--repo"))]
    if words[:1] == ["release"] and words[1:2] and words[1] in ("create", "edit", "delete", "upload", "delete-asset"):
        block("release")
    if "api" not in args:
        return
    paths = [a for a in args if re.match(r"^/?repos/", a)]
    if paths:
        method = http_method(args, "--method", r"^(-f|-F|--field|--raw-field|--input)(=|$)|^-[fF].")
        check_api(paths[0].split("?")[0], method, args)


def check_curl(args: list[str]) -> None:
    urls = [m.group(1) for a in args if (m := re.match(r"^https?://api\.github\.com(/repos/[^?]*)", a))]
    if urls:
        method = http_method(args, "--request", r"^(-d|--data|--data-\w+|--json|-F|--form)(=|$)|^-d.")
        if method == "GET" and any(re.match(r"^(-T|--upload-file)", a) for a in args):
            method = "PUT"
        check_api(urls[0], method, args)


def http_method(args: list[str], long_flag: str, body_flags: str) -> str:
    method, body = None, False
    for k, arg in enumerate(args):
        if arg in ("-X", long_flag) and k + 1 < len(args):
            method = args[k + 1].upper()
        elif arg.startswith(long_flag + "="):
            method = arg.split("=", 1)[1].upper()
        elif match := re.match(r"^-[sSvLkfiIq]*X([A-Za-z]*)$", arg):  # -X, -sSX PUT, -XPUT
            if match.group(1):
                method = match.group(1).upper()
            elif k + 1 < len(args):
                method = args[k + 1].upper()
        elif re.match(body_flags, arg):
            body = True
    return method or ("POST" if body else "GET")


def check_api(path: str, method: str, args: list[str]) -> None:
    if method == "GET":
        return
    if path.endswith("/dispatches"):
        block("workflow dispatch")
    if re.search(r"/actions/(runs|jobs)/\d+/rerun(-failed-jobs)?$", path):
        block("workflow re-run")
    if re.search(r"/pulls/\d+/merge$|/merges$", path):
        block("pull request merge")
    if re.search(r"/git/refs/heads/(develop|main|staging)$", path):
        block("branch update through the API")
    if re.search(r"/git/refs/tags/|/releases(/\d+)?$", path):
        block("tag or release through the API")
    if path.endswith("/git/refs"):
        # Allow only a new claude/... style branch; a body we can't read blocks.
        refs = [m.group(1) for a in args if (m := re.match(r"^(?:-[fF]|--(?:raw-)?field=)?ref=(.+)$", a))]
        if not refs or any(not ref.startswith("refs/heads/") or ref.removeprefix("refs/heads/") in PROTECTED
                           for ref in refs):
            block("tag or long-lived branch created through the API")
    if "/contents/" in path:
        branches = [a.split("=", 1)[1] for a in args if a.startswith("branch=")]
        if not branches or branches[0] in PROTECTED:
            block("commit through the contents API to a long-lived branch")


def check_git(args: list[str], cwd: str, switched: dict[str, str]) -> None:
    git_dir, hooks_path, k = cwd, False, 0
    while k < len(args):
        arg = args[k]
        if arg == "-C" and k + 1 < len(args):
            git_dir, k = resolve(git_dir, args[k + 1]), k + 2
        elif arg == "-c" and k + 1 < len(args):
            hooks_path = hooks_path or args[k + 1].lower().startswith("core.hookspath")
            k += 2
        elif arg in ("--git-dir", "--work-tree", "--namespace"):
            k += 2
        elif arg.startswith("-"):
            k += 1
        else:
            break
    if k >= len(args):
        return
    sub, rest = args[k], args[k + 1:]

    if sub in ("checkout", "switch"):
        # Remember the branch so a later bare `git push` in this command resolves to it.
        if "--" in rest:
            return
        track = any(a in ("-t", "--track") for a in rest)
        for j, arg in enumerate(rest):
            # The new branch's name, wherever the start point sits.
            if arg in ("-b", "-B", "-c", "-C", "--create", "--force-create", "--orphan"):
                if j + 1 < len(rest):
                    switched[git_dir] = rest[j + 1]
                return
        for arg in rest:
            if not arg.startswith("-"):
                name = arg.split("/", 1)[-1] if track else arg
                # `git checkout <path>` restores files and stays on the branch.
                if sub == "switch" or name in PROTECTED or has_ref(git_dir, f"refs/heads/{name}",
                                                                   f"refs/remotes/origin/{name}"):
                    switched[git_dir] = name
                return
        return
    if sub != "push":
        return
    if hooks_path or "--no-verify" in rest:
        block("git push that skips the pre-push hook")
    # A v* tag deploys the frontend and Marketing to production.
    if "--tags" in rest or "--follow-tags" in rest:
        block("git push of tags")

    positional, repo, j = [], False, 0
    while j < len(rest):
        arg = rest[j]
        if arg in ("--all", "--mirror", "--branches"):
            block(f"git push {arg}")
        repo = repo or arg == "--repo" or arg.startswith("--repo=")
        if arg in ("-o", "--push-option", "--repo", "--receive-pack", "--exec"):
            j += 2
            continue
        if not arg.startswith("-"):
            positional.append(arg)
        j += 1
    # positional[0] is the remote unless --repo named it; without a refspec git
    # pushes the current branch.
    refspecs = positional if repo else positional[1:]
    for refspec in refspecs or ["HEAD"]:
        source = refspec.strip().lstrip("+").split(":", 1)[0]
        # v* names the release tags, which may be created earlier in the same command.
        if "refs/tags/" in refspec or re.match(r"^v\d", source) or (source and has_ref(git_dir, f"refs/tags/{source}")):
            block("git push of a tag")
        target = refspec.strip().lstrip("+").rsplit(":", 1)[-1].strip().removeprefix("refs/heads/")
        if target in ("HEAD", "@", "$", ""):  # `$` is an unexpanded $(...)
            target = switched.get(git_dir) or current_branch(git_dir)
        if target in PROTECTED:
            block(f"git push to {target}")


try:
    hook_input = json.load(os.fdopen(3))
except (OSError, ValueError):
    print("cloud-push-guard: could not read the hook input; blocking to stay safe.", file=sys.stderr)
    sys.exit(2)
command = (hook_input.get("tool_input") or {}).get("command") or ""
try:
    check_script(command, hook_input.get("cwd") or os.getcwd(), {})
except Exception as error:  # A crash would exit 1, which lets the command run.
    print(f"cloud-push-guard: could not read the command ({error!r}); blocking to stay safe.", file=sys.stderr)
    sys.exit(2)
PY
