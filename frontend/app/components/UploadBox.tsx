"use client";

type Props = {
  onUpload: (file: File) => void;
  disabled?: boolean;
};

export default function UploadBox({ onUpload, disabled }: Props) {
  return (
    <div className="rounded-2xl border border-dashed border-[var(--dtl-line)] bg-white/75 p-8 shadow-[0_20px_50px_-30px_rgba(18,21,26,0.35)] backdrop-blur-sm transition hover:border-[var(--dtl-copper)]/50">
      <div className="text-center">
        <p className="font-display text-xl font-semibold text-[var(--dtl-ink)]">
          Upload requirements document
        </p>
        <p className="mt-2 text-sm text-[var(--dtl-ink-soft)]">
          PDF or DOCX — large docs are split into sections for fuller ticket
          breakdown
        </p>
        <label
          className={`mt-6 inline-flex cursor-pointer items-center justify-center rounded-xl bg-[var(--dtl-ink)] px-6 py-3 text-sm font-semibold text-[var(--dtl-paper)] transition hover:bg-[var(--dtl-copper-deep)] ${
            disabled ? "pointer-events-none opacity-50" : ""
          }`}
        >
          Choose file
          <input
            type="file"
            accept=".pdf,.docx"
            disabled={disabled}
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.[0]) {
                onUpload(e.target.files[0]);
              }
            }}
          />
        </label>
      </div>
    </div>
  );
}
