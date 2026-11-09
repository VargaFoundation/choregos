// SPDX-License-Identifier: Apache-2.0
"use client";

import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { Empty } from "@/components/ui";
import { cn } from "@/lib/cn";
import type { WordDocument, WordParagraph, WordRun } from "@/lib/office/docx";
import { readOffice, type OfficeDocument } from "@/lib/office";
import type { Presentation, SlideParagraph, SlideShape } from "@/lib/office/pptx";
import { MAX_ROWS, type Workbook } from "@/lib/office/xlsx";

type State = { document: OfficeDocument } | { error: string } | null;

const EMU_PER_POINT = 12700;

/** A font size that follows the slide's width, so a slide reads the same at any size on screen. */
function fontSize(deck: Presentation, points: number): string {
  return `${((points * EMU_PER_POINT) / deck.width) * 100}cqw`;
}

function SlideText({ deck, paragraph }: { deck: Presentation; paragraph: SlideParagraph }) {
  const first = paragraph.runs[0];
  return (
    <p
      style={{
        margin: 0,
        textAlign: paragraph.align,
        paddingLeft: `${paragraph.level * 2.2}cqw`,
        fontSize: first ? fontSize(deck, first.sizePt) : fontSize(deck, 12),
      }}
    >
      {paragraph.bullet && <span aria-hidden>{`${paragraph.bullet} `}</span>}
      {paragraph.runs.map((run, index) => (
        <span
          key={index}
          style={{
            fontSize: fontSize(deck, run.sizePt),
            fontWeight: run.bold ? 700 : undefined,
            fontStyle: run.italic ? "italic" : undefined,
            textDecoration: run.underline ? "underline" : undefined,
            color: run.color,
            fontFamily: run.font ? `"${run.font}", sans-serif` : undefined,
            whiteSpace: "pre-wrap",
          }}
        >
          {run.text}
        </span>
      ))}
    </p>
  );
}

const JUSTIFY: Record<NonNullable<SlideShape["anchor"]>, CSSProperties["justifyContent"]> = {
  top: "flex-start",
  middle: "center",
  bottom: "flex-end",
};

function Shape({ deck, shape }: { deck: Presentation; shape: SlideShape }) {
  const style: CSSProperties = {
    position: "absolute",
    left: `${(shape.x / deck.width) * 100}%`,
    top: `${(shape.y / deck.height) * 100}%`,
    width: `${(shape.width / deck.width) * 100}%`,
    height: `${(shape.height / deck.height) * 100}%`,
    transform: shape.rotation ? `rotate(${shape.rotation}deg)` : undefined,
    backgroundColor: shape.fill,
    border: shape.line ? `1px solid ${shape.line}` : undefined,
    borderRadius: shape.corners === "ellipse" ? "50%" : shape.corners === "round" ? "8%" : undefined,
    display: "flex",
    flexDirection: "column",
    justifyContent: JUSTIFY[shape.anchor ?? "top"],
    overflow: "hidden",
    padding: shape.paragraphs.length > 0 ? "0.6cqw 0.8cqw" : undefined,
  };
  if (shape.image) {
    // eslint-disable-next-line @next/next/no-img-element -- an object URL of raster bytes from the file
    return <img src={shape.image} alt="" style={{ ...style, objectFit: "fill", padding: 0 }} />;
  }
  if (shape.chart) {
    return (
      <div style={{ ...style, border: "1px dashed #999", alignItems: "center", justifyContent: "center", color: "#666", fontSize: "1.2cqw" }} data-testid="office-chart">
        Chart or diagram: open the file to see it
      </div>
    );
  }
  if (shape.table) {
    return (
      <div style={style}>
        <table style={{ width: "100%", borderCollapse: "collapse", color: "#000" }}>
          <tbody>
            {shape.table.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} style={{ border: "1px solid #bbb", padding: "0.3cqw 0.5cqw", verticalAlign: "top" }}>
                    {cell.map((paragraph, p) => (
                      <SlideText key={p} deck={deck} paragraph={paragraph} />
                    ))}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return (
    <div style={style}>
      {shape.paragraphs.map((paragraph, index) => (
        <SlideText key={index} deck={deck} paragraph={paragraph} />
      ))}
    </div>
  );
}

function Deck({ deck }: { deck: Presentation }) {
  return (
    <div className="max-h-[42rem] space-y-3 overflow-y-auto pr-1" data-testid="office-pptx">
      {deck.slides.map((slide, index) => (
        <figure key={index} className="space-y-1" data-testid="office-slide">
          <div
            className="relative w-full overflow-hidden border border-line"
            style={{
              aspectRatio: `${deck.width} / ${deck.height}`,
              containerType: "inline-size",
              backgroundColor: slide.background ?? "#ffffff",
              backgroundImage: slide.backgroundImage ? `url("${slide.backgroundImage}")` : undefined,
              backgroundSize: "cover",
              color: "#000000",
            }}
          >
            {slide.shapes.map((shape, at) => (
              <Shape key={at} deck={deck} shape={shape} />
            ))}
          </div>
          <figcaption className="text-xs text-ink-muted">
            Slide {index + 1} of {deck.slides.length}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

function wordStyle(run: WordRun): CSSProperties {
  return {
    fontWeight: run.bold ? 700 : undefined,
    fontStyle: run.italic ? "italic" : undefined,
    textDecoration: run.underline ? "underline" : undefined,
    color: run.color,
    fontSize: run.sizePt ? `${run.sizePt}pt` : undefined,
    whiteSpace: "pre-wrap",
  };
}

const HEADING_SIZE: Record<number, string> = { 1: "text-2xl", 2: "text-xl", 3: "text-lg" };

function WordLine({ paragraph }: { paragraph: WordParagraph }) {
  const content: ReactNode[] = paragraph.runs.map((run, index) =>
    run.image ? (
      // eslint-disable-next-line @next/next/no-img-element -- an object URL of raster bytes from the file
      <img key={index} src={run.image.url} alt="" width={run.image.width} height={run.image.height} className="inline-block max-w-full" />
    ) : (
      <span key={index} style={wordStyle(run)}>
        {run.text}
      </span>
    ),
  );
  const style: CSSProperties = { textAlign: paragraph.align, marginLeft: paragraph.marker ? `${(paragraph.level + 1) * 1.25}rem` : undefined };
  if (paragraph.heading) {
    return (
      <p role="heading" aria-level={paragraph.heading} className={cn("mt-3 font-semibold", HEADING_SIZE[paragraph.heading] ?? "text-base")} style={style}>
        {content}
      </p>
    );
  }
  return (
    <p style={style} className="min-h-4">
      {paragraph.marker ? <span className="mr-2 text-[#555]">{paragraph.marker}</span> : null}
      {content}
    </p>
  );
}

function Word({ document }: { document: WordDocument }) {
  return (
    <div className="max-h-[42rem] space-y-2 overflow-y-auto border border-line bg-white p-6 text-sm text-black" data-testid="office-docx" data-verbatim>
      {document.blocks.map((block, index) =>
        block.type === "paragraph" ? (
          <WordLine key={index} paragraph={block} />
        ) : (
          <table key={index} className="w-full border-collapse" aria-label="table of the document">
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c} className="border border-[#bbb] p-1 align-top">
                      {cell.map((paragraph, p) => (
                        <WordLine key={p} paragraph={paragraph} />
                      ))}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        ),
      )}
    </div>
  );
}

function Sheets({ workbook }: { workbook: Workbook }) {
  const [active, setActive] = useState(0);
  const sheet = workbook.sheets[active];
  return (
    <div className="space-y-2" data-testid="office-xlsx">
      {workbook.sheets.length > 1 && (
        <div role="tablist" aria-label="sheets" className="flex flex-wrap gap-1">
          {workbook.sheets.map((item, index) => (
            <button
              key={index}
              type="button"
              role="tab"
              aria-selected={index === active}
              onClick={() => setActive(index)}
              className={cn("min-h-8 border border-line px-2 text-xs", index === active ? "bg-accent-soft text-accent-strong" : "text-ink-muted")}
              data-verbatim
            >
              {item.name}
            </button>
          ))}
        </div>
      )}
      {sheet && sheet.rows.length > 0 ? (
        <div className="max-h-[36rem] overflow-auto">
          <table aria-label={`sheet ${sheet.name}`}>
            <tbody>
              {sheet.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c} className="whitespace-nowrap font-mono text-xs" data-verbatim>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>This sheet is empty.</Empty>
      )}
      {sheet?.truncated && (
        <p className="text-xs text-ink-muted" data-testid="office-xlsx-truncated">
          Only the first {MAX_ROWS} rows are shown: download the file for the rest.
        </p>
      )}
    </div>
  );
}

/**
 * A docx, pptx or xlsx drawn in the page (ADR 0129). The file is read in the browser into plain data, and
 * every element here is built from it: no markup, script, macro or external link of the file is used.
 */
export default function OfficeView({ type, blob, fallback }: { type: string; blob: Blob; fallback: ReactNode }) {
  const [state, setState] = useState<State>(null);
  useEffect(() => {
    let alive = true;
    let urls: string[] = [];
    void (async () => {
      const document = await readOffice(type, await blob.arrayBuffer());
      urls = document.urls;
      if (alive) setState({ document });
      else urls.forEach((url) => URL.revokeObjectURL(url));
    })().catch((cause: unknown) => {
      if (alive) setState({ error: cause instanceof Error ? cause.message : "this file cannot be read" });
    });
    return () => {
      alive = false;
      urls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [type, blob]);

  if (state === null) return <Empty>Reading the file…</Empty>;
  if ("error" in state) {
    return (
      <div className="space-y-2">
        <p className="text-xs text-ink-muted" data-testid="office-refused">
          This file cannot be shown in the page: {state.error}.
        </p>
        {fallback}
      </div>
    );
  }
  const { document } = state;
  return (
    <div className="space-y-2" data-testid="office-view">
      <p className="text-xs text-ink-muted" data-testid="office-note">
        A preview: fonts, charts and effects can differ. Download the file for the exact result.
      </p>
      {document.kind === "pptx" && <Deck deck={document} />}
      {document.kind === "docx" && <Word document={document} />}
      {document.kind === "xlsx" && <Sheets workbook={document} />}
    </div>
  );
}
