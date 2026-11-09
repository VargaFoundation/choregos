// SPDX-License-Identifier: Apache-2.0
/** An Office artifact read in the browser, into data the console draws as elements (ADR 0129). */
import { readDocument, type WordDocument } from "./docx";
import { readPresentation, type Presentation } from "./pptx";
import { readWorkbook, type Workbook } from "./xlsx";
import { OfficeFileError, openZip } from "./zip";

export type OfficeDocument = WordDocument | Presentation | Workbook;

export async function readOffice(type: string, buffer: ArrayBuffer): Promise<OfficeDocument> {
  const zip = openZip(buffer);
  if (type === "docx" && zip.has("word/document.xml")) return readDocument(zip);
  if (type === "pptx" && zip.has("ppt/presentation.xml")) return readPresentation(zip);
  if (type === "xlsx" && zip.has("xl/workbook.xml")) return readWorkbook(zip);
  throw new OfficeFileError(`not a ${type} file`);
}
