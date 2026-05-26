import { useEffect, useState } from "react";
import { CheckCircle2, ExternalLink } from "lucide-react";

type Props = {
  prUrl: string;
};

export function PRResultCard({ prUrl }: Props) {
  const [showConfetti, setShowConfetti] = useState(true);

  useEffect(() => {
    const t = window.setTimeout(() => setShowConfetti(false), 2500);
    return () => window.clearTimeout(t);
  }, []);

  return (
    <div className="relative overflow-hidden rounded-lg border border-[#238636] bg-[#0f2d17] p-5">
      {showConfetti && (
        <div className="pointer-events-none absolute inset-0 confetti-layer" aria-hidden />
      )}
      <div className="relative flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-3">
          <CheckCircle2 className="h-8 w-8 shrink-0 text-[#3fb950]" />
          <div>
            <h2 className="text-base font-semibold text-[#3fb950]">
              Pull Request Created Successfully!
            </h2>
            <p className="mt-1 text-sm text-[#8b949e]">
              Your automated fix is ready for review on GitHub.
            </p>
          </div>
        </div>
        <a
          href={prUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center justify-center gap-2 rounded-md bg-[#238636] px-4 py-2 text-sm font-semibold text-white transition hover:bg-[#2ea043]"
        >
          Open Pull Request
          <ExternalLink className="h-4 w-4" />
        </a>
      </div>
    </div>
  );
}
