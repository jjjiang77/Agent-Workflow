"""静态分析工具。

复用 H1 的 analyze_code，主要改动是直接分析内存中的代码字符串
（H2 的代码由 Agent 生成，不一定落成文件），并补充了危险调用检测。
"""
from __future__ import annotations

import ast
import re
from typing import List

SENSITIVE_PATTERNS = [
    (r'(?:password|passwd|pwd)\s*=\s*["\'][^"\']{3,}["\']', "硬编码密码"),
    (r'(?:secret|api_key|apikey|access_key)\s*=\s*["\'][^"\']{5,}["\']', "硬编码密钥"),
    (r'(?:token)\s*=\s*["\'][^"\']{5,}["\']', "硬编码 Token"),
    (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "IP 地址"),
]

RISK_PATTERNS = [
    (r"\beval\s*\(", "eval 动态执行"),
    (r"\bexec\s*\(", "exec 动态执行"),
    (r"os\.system\s*\(", "os.system 执行 shell"),
    (r"subprocess\.(?:call|run|Popen)\s*\(", "子进程调用"),
    (r"pickle\.loads?\s*\(", "pickle 反序列化"),
    (r'open\s*\([^)]*["\']w["\']', "写文件"),
]


def analyze_source(code: str, filename: str = "<generated>") -> str:
    """返回一份可直接塞进 prompt 的文本报告，包含行号。"""
    lines = code.splitlines()
    total = len(lines)
    blank = len([l for l in lines if not l.strip()])
    comments = len([l for l in lines if l.strip().startswith("#")])

    findings: List[str] = []
    for pattern, desc in SENSITIVE_PATTERNS:
        for lineno, line in enumerate(lines, 1):
            if re.search(pattern, line, re.IGNORECASE):
                findings.append(f"  - 第 {lineno} 行：{desc}")

    risks: List[str] = []
    for pattern, desc in RISK_PATTERNS:
        for lineno, line in enumerate(lines, 1):
            if re.search(pattern, line):
                risks.append(f"  - 第 {lineno} 行：{desc}")

    functions: List[str] = []
    syntax = "解析成功"
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = (node.end_lineno - node.lineno + 1) if node.end_lineno else 0
                functions.append(f"  - {node.name}(): 参数 {len(node.args.args)} 个，约 {length} 行")
    except SyntaxError as exc:
        syntax = f"语法错误（第 {exc.lineno} 行）：{exc.msg}"

    report = [
        f"=== 静态分析：{filename} ===",
        f"总行数: {total} | 空行: {blank} | 注释: {comments}",
        f"语法: {syntax}",
        "敏感信息:",
        "\n".join(findings) if findings else "  未发现",
        "危险调用:",
        "\n".join(risks) if risks else "  未发现",
        "函数列表:",
        "\n".join(functions) if functions else "  未检测到函数定义",
    ]
    return "\n".join(report)
