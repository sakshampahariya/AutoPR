export interface ParsedDiffFile {
  filePath: string;
  oldValue: string;
  newValue: string;
}

/**
 * Split a combined unified diff into per-file old/new content for react-diff-viewer.
 */
export function parseUnifiedDiff(diff: string): ParsedDiffFile[] {
  if (!diff.trim()) return [];

  const chunks = diff.split(/(?=^diff --git )/m).filter(Boolean);
  const files: ParsedDiffFile[] = [];

  for (const chunk of chunks) {
    const parsed = parseSingleFileDiff(chunk);
    if (parsed) files.push(parsed);
  }

  if (files.length === 0) {
    const fallback = parseSingleFileDiff(diff);
    if (fallback) files.push(fallback);
  }

  return files;
}

function parseSingleFileDiff(chunk: string): ParsedDiffFile | null {
  const lines = chunk.split("\n");
  let filePath = "file";
  const oldLines: string[] = [];
  const newLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("--- ")) {
      const path = line.slice(4).trim().replace(/^a\//, "");
      if (path !== "/dev/null") filePath = path;
      continue;
    }
    if (line.startsWith("+++ ")) {
      const path = line.slice(4).trim().replace(/^b\//, "");
      if (path !== "/dev/null") filePath = path;
      continue;
    }
    if (line.startsWith("@@")) continue;
    if (line.startsWith("diff --git")) {
      const match = line.match(/ b\/(.+)$/);
      if (match) filePath = match[1];
      continue;
    }
    if (line.startsWith("-") && !line.startsWith("---")) {
      oldLines.push(line.slice(1));
    } else if (line.startsWith("+") && !line.startsWith("+++")) {
      newLines.push(line.slice(1));
    } else if (line.startsWith(" ")) {
      const content = line.slice(1);
      oldLines.push(content);
      newLines.push(content);
    }
  }

  if (oldLines.length === 0 && newLines.length === 0) return null;

  return {
    filePath,
    oldValue: oldLines.join("\n"),
    newValue: newLines.join("\n"),
  };
}
