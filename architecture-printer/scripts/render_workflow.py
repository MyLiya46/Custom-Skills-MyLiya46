#!/usr/bin/env python3
"""把 scan_project.py 的 JSON 渲染成零依赖、可离线打开的交互式 HTML。"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path
from typing import Any


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def layer_for(item: dict[str, Any], routes: set[tuple[str, int]]) -> str:
    path = item.get("file", "").lower()
    if any((item.get("file", ""), int(item.get("line", 0))) in routes for _ in (0,)):
        return "入口层"
    if any(x in path for x in ("route", "main.", "cli", "command", "index.")):
        return "入口层"
    if any(x in path for x in ("db", "model", "schema", "migration", "repository", "config", "settings")):
        return "数据与配置层"
    if item.get("kind") == "module" and item.get("external"):
        return "外部依赖"
    if any(x in path for x in ("service", "planner", "orchestr", "controller", "workflow", "use_case", "store")):
        return "编排层"
    if any(x in path for x in ("client", "adapter", "worker", "task", "job", "api", "gateway", "provider")):
        return "执行与外部层"
    return "执行与外部层"


def source_href(target: Path, output: Path, file_name: str, line: int) -> str:
    source = target / Path(file_name)
    try:
        value = os.path.relpath(source, output.parent).replace(os.sep, "/")
    except ValueError:
        value = source.as_uri()
    return f"{value}#L{line}"


def node_data(scan: dict[str, Any], output: Path) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    target = Path(scan.get("target_dir", ".")).resolve()
    routes = {(r.get("file", ""), int(r.get("line", 0))) for r in scan.get("routes", [])}
    grouped: dict[str, list[dict[str, Any]]] = {"入口层": [], "编排层": [], "执行与外部层": [], "数据与配置层": [], "外部依赖": []}
    seen = set()
    for item in scan.get("declarations", []):
        key = (item.get("file"), item.get("line"), item.get("name"), item.get("kind"))
        if key in seen:
            continue
        seen.add(key)
        row = dict(item)
        row["layer"] = layer_for(row, routes)
        row["href"] = source_href(target, output, row.get("file", ""), int(row.get("line", 1)))
        row["route_text"] = ", ".join(f"{r.get('method')} {r.get('path')}" for r in item.get("routes", []))
        grouped[row["layer"]].append(row)
    present_routes = {(item.get("file"), str(item.get("line")), item.get("name")) for item in all_nodes_from(grouped)}
    for route in scan.get("routes", []):
        key = (route.get("file"), str(route.get("line")), route.get("function"))
        if key in present_routes:
            continue
        row = {"file": route.get("file", ""), "line": int(route.get("line", 1) or 1), "name": route.get("function", "[UNKNOWN: handler]"), "kind": "route", "visibility": "public", "signature": "[UNKNOWN: handler]", "docstring": "由框架入口语法识别；handler 未能静态追踪。", "async": False, "layer": "入口层", "href": source_href(target, output, route.get("file", ""), int(route.get("line", 1) or 1)), "route_text": f"{route.get('method', 'ROUTE')} {route.get('path', '')}"}
        grouped["入口层"].append(row)
    declared_files = {item.get("file") for item in scan.get("declarations", [])}
    for file_name in scan.get("modules", []):
        if file_name in declared_files:
            continue
        external = file_name.startswith("[UNKNOWN:")
        row = {"file": file_name, "line": 1, "name": Path(file_name).name, "kind": "module", "visibility": "module", "signature": "", "docstring": "", "async": False, "external": external, "layer": "外部依赖" if external else layer_for({"file": file_name, "kind": "module"}, routes), "href": source_href(target, output, file_name, 1), "route_text": ""}
        grouped[row["layer"]].append(row)
    for path, info in scan.get("configs", {}).items():
        row = {"file": path, "line": 1, "name": Path(path).name, "kind": "config", "visibility": "config", "signature": "", "docstring": "配置键与服务提示", "async": False, "layer": "数据与配置层", "href": source_href(target, output, path, 1), "route_text": "", "config": info}
        grouped["数据与配置层"].append(row)
    return grouped, [*scan.get("edges", [])]


def all_nodes_from(grouped: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [item for values in grouped.values() for item in values]


def node_button(item: dict[str, Any], index: int) -> str:
    route = f"<span class='route'>{esc(item.get('route_text'))}</span>" if item.get("route_text") else ""
    status = "外部/未解析" if item.get("external") or str(item.get("file", "")).startswith("[UNKNOWN:") else item.get("kind", "module")
    return f"<button class='node {'external' if item.get('external') else ''}' data-index='{index}'><strong>{esc(item.get('name'))}</strong><span>{esc(item.get('file'))}:{esc(item.get('line'))} · {esc(status)}</span>{route}</button>"


def layer_html(name: str, items: list[dict[str, Any]], offset: int, all_nodes: list[dict[str, Any]]) -> str:
    count = len(items)
    title = f"{name} <small>{count} 个可展开对象</small>"
    chunks = []
    if count > 80:
        chunks.append(f"<details class='subsystem' open><summary>{esc(name)} 子系统 <small>{count} 个节点，按索引保留全部入口</small></summary><div class='nodes'>")
        chunks.extend(node_button(item, all_nodes.index(item)) for item in items)
        chunks.append("</div></details>")
    else:
        chunks.append("<div class='nodes'>")
        chunks.extend(node_button(item, offset + i) for i, item in enumerate(items))
        chunks.append("</div>")
    return f"<section class='layer'><details open><summary>{title}</summary>{''.join(chunks)}</details></section>"


def edge_rows(edges: list[dict[str, Any]]) -> str:
    rows = []
    for edge in edges:
        target = edge.get("target", "[UNKNOWN: target]")
        kind = "async" if edge.get("async") else "sync"
        rows.append(f"<tr><td>{esc(edge.get('file'))}</td><td>→</td><td class='{kind}'>{esc(target)}</td><td>{kind}</td></tr>")
    if not rows:
        return "<tr><td colspan='4'>未识别到 import 边</td></tr>"
    return "".join(rows)


def render(scan: dict[str, Any], output: Path) -> str:
    output = output.resolve()
    grouped, edges = node_data(scan, output)
    all_nodes = [item for values in grouped.values() for item in values]
    routes = scan.get("routes", [])
    route_keys = {(r.get("file"), str(r.get("line")), r.get("function")) for r in routes}
    node_route_keys = {(item.get("file"), str(item.get("line")), item.get("name")) for item in all_nodes}
    route_found = sum(1 for route in routes if (route.get("file"), str(route.get("line")), route.get("function")) in node_route_keys)
    route_total = len(routes)
    stats = scan.get("stats", {})
    framework = scan.get("framework", {})
    data_json = json.dumps({"nodes": all_nodes, "edges": edges, "scan": scan}, ensure_ascii=False).replace("</", "<\\/")
    layers = []
    offset = 0
    for name in ("入口层", "编排层", "执行与外部层", "数据与配置层", "外部依赖"):
        layers.append(layer_html(name, grouped[name], offset, all_nodes))
        offset += len(grouped[name])
    html_text = f'''<!doctype html>
<html lang="zh-CN" data-theme="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="{esc(Path(scan.get("target_dir", "project")).name)} 架构速查图"><title>{esc(Path(scan.get("target_dir", "project")).name)} · 架构工作流</title>
<style>
:root{{--bg:#0e131e;--panel:#161d2b;--panel2:#1d2636;--text:#e7ecf5;--muted:#9aa5b8;--border:#2a3446;--accent:#7dd3fc;--internal:#34d3b0;--external:#fbbf24;--unknown:#f87171}}:root[data-theme=light]{{--bg:#f5f7fb;--panel:#fff;--panel2:#eef1f7;--text:#1a2233;--muted:#5b6473;--border:#d9e0eb;--accent:#0369a1;--internal:#087f72;--external:#a16207;--unknown:#b91c1c}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}}code,pre,.mono{{font-family:Consolas,monospace}}code{{color:var(--accent)}}header{{position:sticky;top:0;z-index:3;display:flex;gap:10px;align-items:center;padding:12px 22px;background:var(--panel);border-bottom:1px solid var(--border)}}header strong{{white-space:nowrap}}header small{{color:var(--muted)}}input,button{{font:inherit}}input{{margin-left:auto;width:min(320px,35vw);padding:7px 9px;background:var(--panel2);border:1px solid var(--border);color:var(--text);border-radius:7px}}button{{cursor:pointer}}.toggle{{padding:6px 9px;background:var(--panel2);border:1px solid var(--border);color:var(--text);border-radius:7px}}main{{max-width:1280px;margin:auto;padding:24px 22px 48px}}h1{{margin:0 0 4px;font-size:24px}}.lead,.note{{color:var(--muted)}}.note{{border-left:3px solid var(--unknown);padding:8px 11px;background:var(--panel);margin:14px 0}}.overview{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:18px 0}}.stage{{padding:12px;background:var(--panel);border:1px solid var(--border);border-top:3px solid var(--accent);border-radius:8px}}.stage strong{{display:block}}.stage span{{font-size:11px;color:var(--muted)}}.legend{{display:flex;flex-wrap:wrap;gap:12px;color:var(--muted);font-size:11px;margin:8px 0 18px}}.dot{{display:inline-block;width:9px;height:9px;border-radius:3px;background:var(--internal);margin-right:4px}}.dot.ext{{background:var(--external)}}.line{{display:inline-block;width:22px;border-top:2px solid var(--muted);margin:0 4px 3px 0}}.line.async{{border-top-style:dashed;border-color:var(--accent)}}.layers{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.layer{{background:var(--panel);border:1px solid var(--border);border-radius:10px;overflow:hidden}}details>summary{{cursor:pointer;padding:11px 13px;font-weight:800;color:var(--accent)}}summary small{{color:var(--muted);font-weight:400;font-size:11px}}.nodes{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;padding:0 10px 11px}}.node{{text-align:left;min-width:0;border:1px solid var(--border);border-left:3px solid var(--internal);background:var(--panel2);color:var(--text);padding:7px 8px;border-radius:6px}}.node:hover,.node:focus{{border-color:var(--accent);outline:0;box-shadow:0 0 0 2px #7dd3fc33}}.node.external{{border-left-color:var(--external)}}.node strong,.node span,.node .route{{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.node strong{{font-size:12px}}.node span,.node .route{{font-size:10.5px;color:var(--muted)}}.node .route{{color:var(--accent);margin-top:2px}}.subsystem{{margin:0 10px 11px;border:1px dashed var(--border);border-radius:7px}}.subsystem summary{{font-size:12px}}.subsystem .nodes{{padding-top:7px}}.panel{{margin-top:16px;background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:13px}}.panel h2{{font-size:15px;margin:0 0 9px}}table{{width:100%;border-collapse:collapse;font-size:11px}}th,td{{padding:7px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}}th{{color:var(--muted);background:var(--panel2)}}td.async{{color:var(--accent)}}td.sync{{color:var(--internal)}}td:nth-child(3){{color:var(--muted)}}.drawer{{position:fixed;inset:0;z-index:5;display:none;background:#0008}}.drawer.open{{display:block}}.sheet{{position:absolute;right:0;top:0;width:min(560px,94vw);height:100%;overflow:auto;background:var(--panel);padding:20px;box-shadow:-8px 0 24px #0006}}.close{{float:right;padding:4px 9px;background:var(--panel2);color:var(--text);border:1px solid var(--border);border-radius:6px}}.sheet h2{{margin:4px 50px 4px 0;overflow-wrap:anywhere}}.sheet p{{color:var(--muted)}}.sheet pre{{white-space:pre-wrap;background:var(--panel2);padding:9px;border-radius:6px;font-size:11px;overflow:auto}}.sheet a{{color:var(--accent)}}.sheet h3{{font-size:12px;border-top:1px dashed var(--border);padding-top:11px}}@media(max-width:850px){{.overview,.layers{{grid-template-columns:1fr}}.nodes{{grid-template-columns:1fr}}header{{flex-wrap:wrap}}input{{order:3;margin-left:0;width:100%}}}}
有效框架证据为：<code>{esc(framework.get("label", "通用项目"))}</code> · 模板路由：<code>{esc(framework.get("template", "generic"))}</code> · 识别方式：<code>{esc(framework.get("mode", "unknown"))}</code></p><p class="note">证据规则：导入链来自静态扫描；未解析对象保持 <code>[UNKNOWN: name]</code>。页面不展示配置值、token 或密钥。{(" 扫描因项目超过 5000 文件而缩减。" if stats.get("reduced_for_size") else "")}</p><div class="overview"><div class="stage" style="--accent:#f97316"><strong>入口层</strong><span>route / main / CLI</span></div><div class="stage" style="--accent:#a78bfa"><strong>编排层</strong><span>service / planner / controller</span></div><div class="stage" style="--accent:#2dd4bf"><strong>执行与外部</strong><span>client / adapter / provider</span></div><div class="stage" style="--accent:#94a3b8"><strong>数据与配置</strong><span>db / model / schema / config</span></div><div class="stage" style="--accent:#cbd5e1"><strong>返回</strong><span>response / UI / downstream</span></div></div><div class="legend"><span><i class="dot"></i>内部对象</span><span><i class="dot ext"></i>外部或未解析对象</span><span><i class="line"></i>同步 import / 依赖</span><span><i class="line async"></i>异步关系（仅扫描确认时）</span></div><div class="layers">{''.join(layers)}</div><section class="panel"><h2>导入关系与证据边</h2><table><thead><tr><th>来源文件</th><th></th><th>目标</th><th>关系</th></tr></thead><tbody>{edge_rows(edges)}</tbody></table></section><p class="note">解析错误 / 待确认项：{esc('; '.join(f"{e.get('file')}: {e.get('detail')}" for e in scan.get('errors', [])) or '无')}</p></main><aside class="drawer" id="drawer"><div class="sheet"><button class="close" id="close">关闭</button><div id="detail"></div></div></aside><noscript><main><h1>架构流程（无脚本降级）</h1><pre>入口 → 编排 → 执行/外部 → 数据与配置 → 返回\n\n公开 route、函数签名、导入边和配置键请查看本页的分层索引。</pre></main></noscript><script type="application/json" id="scan-data">{data_json}</script><script>(function(){{var data=JSON.parse(document.getElementById('scan-data').textContent),drawer=document.getElementById('drawer'),detail=document.getElementById('detail');function esc(s){{return String(s||'').replace(/[&<>"']/g,function(c){{return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]}})}}function show(i){{var n=data.nodes[i]||{{}};var c=n.config||{{}};detail.innerHTML='<h2>'+esc(n.name)+'</h2><p>'+esc(n.file)+':'+esc(n.line)+' · '+esc(n.kind)+'</p><h3>签名 / 路由</h3><pre>'+esc(n.signature||n.route_text||'[UNKNOWN: signature]')+'</pre><h3>docstring / 说明</h3><p>'+esc(n.docstring||'未识别 docstring')+'</p><h3>证据与源码</h3><p><a href="'+esc(n.href)+'">打开 '+esc(n.file)+':'+esc(n.line)+'</a></p>'+(n.route_text?'<p><code>'+esc(n.route_text)+'</code></p>':'')+(n.config?'<h3>配置元数据（仅键名/host）</h3><pre>'+esc(JSON.stringify(c,null,2))+'</pre>':'')+'<p class="note">未识别内容保留 [UNKNOWN]，请回到源码确认。</p>';drawer.classList.add('open')}}document.querySelectorAll('.node').forEach(function(b){{b.addEventListener('click',function(){{show(Number(b.dataset.index))}})}});document.getElementById('close').onclick=function(){{drawer.classList.remove('open')}};drawer.onclick=function(e){{if(e.target===drawer)drawer.classList.remove('open')}};document.getElementById('search').oninput=function(e){{var q=e.target.value.toLowerCase();document.querySelectorAll('.node').forEach(function(b){{b.hidden=q&&!b.textContent.toLowerCase().includes(q)}})}};var t=document.getElementById('theme');t.onclick=function(){{var n=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=n;try{{localStorage.setItem('architecture-theme',n)}}catch(e){{}}}};try{{var n=localStorage.getItem('architecture-theme');if(n)document.documentElement.dataset.theme=n}}catch(e){{}}}})();</script></body></html>'''
    prefix = ("</style></head><body><header><strong>" + esc(Path(scan.get("target_dir", "project")).name) + " <small>· 架构工作流</small></strong><input id=\"search\" placeholder=\"过滤文件 / 函数 / route…\"><button class=\"toggle\" id=\"theme\">切换主题</button></header><main><h1>项目架构总览</h1><p class=\"lead\">扫描范围：<code>" + esc(scan.get("scope")) + "</code> · 文件 " + str(stats.get("scanned_files", 0)) + " · 行数 " + str(stats.get("lines", 0)) + " · 函数/类 " + str(stats.get("declarations", 0)) + " · route 覆盖 " + str(route_found) + "/" + str(route_total) + "<br>有效框架证据为：")
    html_text = html_text.replace("有效框架证据为：", prefix, 1)
    return html_text


def main() -> int:
    parser = argparse.ArgumentParser(description="Render an offline interactive architecture workflow")
    parser.add_argument("scan_json", nargs="?", default="-", help="JSON file or - for stdin")
    parser.add_argument("-o", "--output", required=True, type=Path)
    args = parser.parse_args()
    raw = sys.stdin.read() if args.scan_json == "-" else Path(args.scan_json).read_text(encoding="utf-8")
    scan = json.loads(raw)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(scan, args.output), encoding="utf-8")
    print(f"已生成 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
