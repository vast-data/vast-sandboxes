import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getWorkflowAgents } from "../../api";
import { artifacts, humanBytes, sandboxInfo } from "../../attrs";
import { usePolling } from "../../hooks/usePolling";
import type { AgentLiveness, ArtifactFile, GraphNode, WorkflowStorageState } from "../../types";
import { CubeIcon, DownloadIcon, FileIcon, TerminalIcon } from "../icons";
import { Markdown } from "../Markdown";
import { AgentShell } from "./AgentShell";

interface Props {
  workflowId: string;
  subAgents: GraphNode[];
  status: string;
  storage?: WorkflowStorageState | null;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  onOpenStorage?: () => void;
}

interface FlatFile {
  key: string;
  agent: string;
  slug: string;
  nodeId: string;
  isBlock: boolean;
  file: ArtifactFile;
}

interface TreeDir {
  name: string;
  path: string;
  dirs: TreeDir[];
  files: FlatFile[];
}

function buildTree(files: FlatFile[], rootKey: string): TreeDir {
  const root: TreeDir = { name: "", path: rootKey, dirs: [], files: [] };
  for (const f of files) {
    const parts = f.file.rel.split("/").filter(Boolean);
    let cur = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const seg = parts[i];
      const p = `${cur.path}/${seg}`;
      let d = cur.dirs.find((x) => x.name === seg);
      if (!d) {
        d = { name: seg, path: p, dirs: [], files: [] };
        cur.dirs.push(d);
      }
      cur = d;
    }
    cur.files.push(f);
  }
  const sortRec = (d: TreeDir) => {
    d.dirs.sort((a, b) => a.name.localeCompare(b.name));
    d.files.sort((a, b) => a.file.rel.localeCompare(b.file.rel));
    d.dirs.forEach(sortRec);
  };
  sortRec(root);
  return root;
}

function countFiles(d: TreeDir): number {
  return d.files.length + d.dirs.reduce((n, c) => n + countFiles(c), 0);
}

function leafName(rel: string): string {
  const parts = rel.split("/").filter(Boolean);
  return parts[parts.length - 1] || rel;
}

function download(file: ArtifactFile) {
  const blob = new Blob([file.content ?? ""], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = file.name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

function slugOf(node: GraphNode, workflowId: string): string {
  const s = node.attributes.slug;
  if (s) return s;
  const pvc = node.attributes.pvc_name ?? "";
  const prefix = `${workflowId}-`;
  return pvc.startsWith(prefix) ? pvc.slice(prefix.length) : node.agent_name;
}

const TREE_W_KEY = "vastclaw_artifact_tree_w";
const TREE_MIN = 200;
const TREE_MAX = 720;

export function ArtifactsView({
  workflowId,
  subAgents,
  status,
  storage,
  selectedId,
  onSelect,
  onOpenStorage,
}: Props) {
  const [live, setLive] = useState<AgentLiveness[]>([]);
  const [selKey, setSelKey] = useState<string | null>(null);
  const [shellSlug, setShellSlug] = useState<string | null>(null);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const autoOpenedRef = useRef<Set<string>>(new Set());

  function toggleDir(path: string) {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  const [treeWidth, setTreeWidth] = useState<number>(() => {
    const v = Number(localStorage.getItem(TREE_W_KEY));
    return v >= TREE_MIN && v <= TREE_MAX ? v : 300;
  });
  const dragRef = useRef<{ startX: number; startW: number } | null>(null);

  useEffect(() => {
    localStorage.setItem(TREE_W_KEY, String(treeWidth));
  }, [treeWidth]);

  function onResizeStart(e: React.MouseEvent) {
    e.preventDefault();
    dragRef.current = { startX: e.clientX, startW: treeWidth };
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      const dx = ev.clientX - dragRef.current.startX;
      setTreeWidth(Math.min(TREE_MAX, Math.max(TREE_MIN, dragRef.current.startW + dx)));
    };
    const onUp = () => {
      dragRef.current = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }

  const load = useCallback(() => {
    getWorkflowAgents(workflowId)
      .then(setLive)
      .catch(() => setLive([]));
  }, [workflowId]);

  useEffect(() => {
    load();
  }, [load]);
  usePolling(load, status === "running" ? 4000 : 20000, true);

  const liveBySlug = useMemo(() => {
    const m = new Map<string, AgentLiveness>();
    for (const a of live) m.set(a.slug, a);
    return m;
  }, [live]);

  const groups = useMemo(
    () =>
      subAgents.map((node) => {
        const slug = slugOf(node, workflowId);
        const sb = sandboxInfo(node);
        const files = artifacts(node);
        return {
          node,
          slug,
          sb,
          files: files.map<FlatFile>((file) => ({
            key: `${node.id}::${file.rel}`,
            agent: node.agent_name,
            slug,
            nodeId: node.id,
            isBlock: sb.isBlock,
            file,
          })),
        };
      }),
    [subAgents, workflowId],
  );

  const byKey = useMemo(() => {
    const m = new Map<string, FlatFile>();
    for (const g of groups) for (const f of g.files) m.set(f.key, f);
    return m;
  }, [groups]);

  const totals = useMemo(() => {
    const block = groups.filter((g) => g.sb.isBlock).length;
    const files = groups.reduce((n, g) => n + g.files.length, 0);
    const liveCount = groups.filter((g) => liveBySlug.get(g.slug)?.alive).length;
    return { block, files, liveCount, agents: groups.length };
  }, [groups, liveBySlug]);

  useEffect(() => {
    const toOpen: string[] = [];
    for (const g of groups) {
      for (const d of buildTree(g.files, g.node.id).dirs) {
        if (!autoOpenedRef.current.has(d.path)) {
          autoOpenedRef.current.add(d.path);
          toOpen.push(d.path);
        }
      }
    }
    if (toOpen.length) setExpandedDirs((prev) => new Set([...prev, ...toOpen]));
  }, [groups]);

  useEffect(() => {
    if (selKey && byKey.has(selKey)) return;
    const first = groups.flatMap((g) => g.files)[0];
    setSelKey(first ? first.key : null);
  }, [groups, byKey, selKey]);

  useEffect(() => {
    if (!selectedId) return;
    const g = groups.find((x) => x.node.id === selectedId);
    if (!g || g.files.length === 0) return;
    const cur = selKey ? byKey.get(selKey) : undefined;
    if (cur && cur.slug === g.slug) return;
    setShellSlug(null);
    setSelKey(g.files[0].key);
  }, [selectedId, groups, byKey, selKey]);

  const selected = selKey ? byKey.get(selKey) : undefined;
  const selectedGroup = selected ? groups.find((g) => g.slug === selected.slug) : undefined;
  const shellAgent = shellSlug ? groups.find((g) => g.slug === shellSlug) : undefined;

  function renderFile(f: FlatFile, depth: number) {
    return (
      <button
        key={f.key}
        className={`tree-file ${selKey === f.key && !shellSlug ? "sel" : ""}`}
        style={{ paddingLeft: 8 + depth * 14 }}
        onClick={() => {
          setShellSlug(null);
          setSelKey(f.key);
          onSelect?.(f.nodeId);
        }}
        title={f.file.path}
      >
        <FileIcon size={13} className="tf-icon" />
        <span className="tf-name">{leafName(f.file.rel)}</span>
        <span className="tf-size">{humanBytes(f.file.bytes)}</span>
      </button>
    );
  }

  function renderDir(dir: TreeDir, depth: number): React.ReactNode {
    return (
      <>
        {dir.dirs.map((d) => {
          const isOpen = expandedDirs.has(d.path);
          return (
            <div key={d.path} className="tree-dir">
              <button
                className="tree-dir-head"
                style={{ paddingLeft: 8 + depth * 14 }}
                onClick={() => toggleDir(d.path)}
                title={d.name}
              >
                <span className={`tree-caret ${isOpen ? "" : "closed"}`}>▾</span>
                <span className="tree-dir-name">{d.name}</span>
                <span className="tf-size">{countFiles(d)}</span>
              </button>
              {isOpen && renderDir(d, depth + 1)}
            </div>
          );
        })}
        {dir.files.map((f) => renderFile(f, depth))}
      </>
    );
  }

  const clonedCount = groups.filter((g) => g.sb.cloneSourcePvc).length;

  if (groups.length === 0) {
    return (
      <div className="artifacts">
        <p className="muted">
          No OpenShell sandboxes yet — the research-lead is still planning in the platform.
          At fan-out (after plan approval) each sub-agent is created through the OpenShell
          gateway under a NemoClaw policy, on its own VAST block volume; files it writes
          show up here. The lead&apos;s workspace is the lead (head) PVC on the Storage tab.
        </p>
      </div>
    );
  }

  return (
    <div className="artifacts">
      <div className="sandbox-banner">
        <div className="sandbox-banner-lead">
          <CubeIcon size={18} />
          <div>
            <strong>OpenShell sandboxes</strong>
            <span>
              Every sub-agent is a NemoClaw / OpenClaw agent inside an OpenShell sandbox
              (dedicated VAST block PVC). The research-lead orchestrates in the platform, so
              it has no sandbox here — its workspace is the lead (head) PVC on the Storage
              tab.
              {storage?.clone_enabled
                ? " Volumes are zero-copy clones of the lead workspace at spawn."
                : ""}
              {storage?.last_checkpoint
                ? ` Last checkpoint: ${storage.last_checkpoint}.`
                : ""}
            </span>
          </div>
        </div>
        <div className="sandbox-banner-stats">
          {onOpenStorage && storage && (
            <button
              type="button"
              className="sb-storage-link"
              onClick={onOpenStorage}
              title="VolumeSnapshots, PVC lineage, restore from checkpoint"
            >
              Storage → checkpoints
            </button>
          )}
          <span className="sb-stat">
            <b>{totals.agents}</b> OpenShell sandboxes
          </span>
          <span className="sb-stat block">
            <b>{totals.block}</b> block-backed
          </span>
          {clonedCount > 0 && (
            <span className="sb-stat on">
              <b>{clonedCount}</b> cloned
            </span>
          )}
          {storage?.snapshots.length ? (
            <span className="sb-stat on">
              <b>{storage.snapshots.length}</b> snapshots
            </span>
          ) : null}
          <span className={`sb-stat ${totals.files ? "on" : ""}`}>
            <b>{totals.files}</b> files
          </span>
          <span className={`sb-stat ${totals.liveCount ? "on" : ""}`}>
            <b>{totals.liveCount}</b> live
          </span>
        </div>
      </div>

      <div
        className="artifacts-notebook"
        style={{ ["--tree-w" as string]: `${treeWidth}px` } as React.CSSProperties}
      >
        {/* Left: every file across every sandbox, grouped by agent. */}
        <aside className="artifact-tree">
          {groups.map((g) => {
            const alive = !!liveBySlug.get(g.slug)?.alive;
            return (
              <div
                key={g.node.id}
                className={`tree-group ${g.sb.isBlock ? "is-block" : ""} ${
                  selectedId === g.node.id ? "sel" : ""
                }`}
              >
                <div className="tree-group-head">
                  <button
                    className="tg-name"
                    title={`${g.node.agent_name} — select in graph`}
                    onClick={() => onSelect?.(g.node.id)}
                  >
                    {g.node.agent_name}
                  </button>
                  {alive ? (
                    <button
                      className="tg-shell"
                      title="Open a live shell into this OpenShell sandbox"
                      onClick={() => setShellSlug(shellSlug === g.slug ? null : g.slug)}
                    >
                      <TerminalIcon size={12} />
                    </button>
                  ) : (
                    <span className="tg-count">{g.files.length}</span>
                  )}
                </div>
                <div className="tg-chips">
                  <span
                    className="tg-chip openshell"
                    title="Created via OpenShell gateway under NemoClaw policy"
                  >
                    OpenShell
                  </span>
                  <span className={`tg-chip ${g.sb.isBlock ? "block" : "file"}`}>
                    {g.sb.isBlock ? "Block" : "File"}
                  </span>
                  {g.sb.accessMode && <span className="tg-chip">{g.sb.accessMode}</span>}
                  {g.sb.cloneSourcePvc && (
                    <span className="tg-chip clone" title="Zero-copy block clone at spawn">
                      clone ← {g.sb.cloneSourcePvc}
                    </span>
                  )}
                  {alive && <span className="tg-chip liveish">live</span>}
                </div>
                {g.sb.pvc && (
                  <div className="tg-map" title="PersistentVolumeClaim mounted as the sandbox's block device">
                    <CubeIcon size={11} className="tg-map-icon" />
                    <code className="tg-map-pvc">{g.sb.pvc}</code>
                    <span className="tg-map-arrow">→</span>
                    <code className="tg-map-mount">{g.sb.mount || "/workspace"}</code>
                    {g.sb.storageClass && <span className="tg-map-sc">{g.sb.storageClass}</span>}
                  </div>
                )}
                {g.files.length === 0 ? (
                  <div className="tg-empty">no files{alive ? " yet" : ""}</div>
                ) : (
                  <div className="tree-files">{renderDir(buildTree(g.files, g.node.id), 0)}</div>
                )}
              </div>
            );
          })}
        </aside>

        {/* Drag to widen the tree so long artifact paths are fully readable. */}
        <div
          className="artifact-resizer"
          role="separator"
          aria-orientation="vertical"
          title="Drag to resize · double-click to reset"
          onMouseDown={onResizeStart}
          onDoubleClick={() => setTreeWidth(300)}
        >
          <span className="artifact-resizer-grip" />
        </div>

        {/* Right: the reader — a live shell, a file, or an empty prompt. */}
        <section className="artifact-reader">
          {shellAgent ? (
            <AgentShell
              workflowId={workflowId}
              slug={shellAgent.slug}
              agentName={shellAgent.node.agent_name}
              onClose={() => setShellSlug(null)}
            />
          ) : selected ? (
            <>
              <div className="reader-head">
                <div className="reader-title">
                  <FileIcon size={14} />
                  <span className="reader-path" title={selected.file.path}>
                    {selected.file.rel}
                  </span>
                </div>
                <div className="reader-meta">
                  <span>{selected.agent}</span>
                  <span className="dot-sep">·</span>
                  <span>{humanBytes(selected.file.bytes)}</span>
                  <span className="dot-sep">·</span>
                  <span className={`rk k-${selected.file.kind}`}>{selected.file.kind}</span>
                  {typeof selected.file.content === "string" && (
                    <button className="link-btn" onClick={() => download(selected.file)}>
                      <DownloadIcon size={13} /> Download
                    </button>
                  )}
                </div>
              </div>
              {selectedGroup?.sb.pvc && (
                <div className="reader-mapping" title="This file lives on the OpenShell sandbox's isolated VAST volume">
                  <CubeIcon size={12} />
                  <span className="rm-label">OpenShell volume</span>
                  <code className="tg-map-pvc">{selectedGroup.sb.pvc}</code>
                  <span className="tg-map-arrow">→</span>
                  <code className="tg-map-mount">{selectedGroup.sb.mount || "/workspace"}</code>
                  <span className={`tg-chip ${selectedGroup.sb.isBlock ? "block" : "file"}`}>
                    {selectedGroup.sb.isBlock ? "Block" : "File"}
                  </span>
                  {selectedGroup.sb.storageClass && (
                    <span className="tg-map-sc">{selectedGroup.sb.storageClass}</span>
                  )}
                  {selectedGroup.sb.accessMode && (
                    <span className="tg-map-sc">{selectedGroup.sb.accessMode}</span>
                  )}
                </div>
              )}
              <div className="reader-body">
                {typeof selected.file.content === "string" ? (
                  selected.file.kind === "md" ? (
                    <Markdown text={selected.file.content} />
                  ) : (
                    <pre>{selected.file.content}</pre>
                  )
                ) : (
                  <p className="muted">
                    This file lives on the OpenShell sandbox PVC but wasn't inlined (binary or too large).
                    {liveBySlug.get(selected.slug)?.alive
                      ? " Open a shell on this OpenShell sandbox to inspect it."
                      : ""}
                  </p>
                )}
              </div>
            </>
          ) : (
            <div className="reader-empty">
              {totals.files === 0
                ? "No files captured yet. OpenShell agents write findings to their sandbox as they complete — they'll appear here."
                : "Select a file from the left to view it."}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
