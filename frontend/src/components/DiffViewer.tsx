import { useState } from "react";
import ReactDiffViewer from "react-diff-viewer-continued";
import { ChevronDown, ChevronRight } from "lucide-react";
import { parseUnifiedDiff } from "../lib/diff";

type Props = {
  diff: string;
};

export function DiffViewer({ diff }: Props) {
  const files = parseUnifiedDiff(diff);

  if (files.length === 0) {
    return (
      <div className="rounded-lg border border-[#30363d] bg-[#161b22] p-4">
        <p className="text-sm text-[#8b949e]">No diff to display.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold text-[#e6edf3]">Code changes</h2>
      {files.map((file) => (
        <FileDiffBlock key={file.filePath} file={file} />
      ))}
    </div>
  );
}

function FileDiffBlock({
  file,
}: {
  file: { filePath: string; oldValue: string; newValue: string };
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="overflow-hidden rounded-lg border border-[#30363d] bg-[#161b22]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 border-b border-[#30363d] bg-[#21262d] px-4 py-2 text-left text-sm font-mono text-[#58a6ff] hover:bg-[#30363d]"
      >
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0" />
        )}
        {file.filePath}
      </button>
      {open && (
        <div className="max-h-96 overflow-auto text-sm">
          <ReactDiffViewer
            oldValue={file.oldValue}
            newValue={file.newValue}
            splitView
            useDarkTheme
            hideLineNumbers={false}
          />
        </div>
      )}
    </div>
  );
}
