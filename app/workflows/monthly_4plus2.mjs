import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const modulesRoot = process.env.ZQC_ARTIFACT_NODE_MODULES;
if (!modulesRoot) throw new Error("缺少 ZQC_ARTIFACT_NODE_MODULES");
const require = createRequire(path.join(modulesRoot, "package.json"));
const { FileBlob, SpreadsheetFile, Workbook } = require("@oai/artifact-tool");
const config = JSON.parse(await fs.readFile(process.argv[2], "utf8"));
const { sources, refs, positions, batch, outputDir } = config;
const year = Number(batch.slice(0, 4));
const month = Number(batch.slice(4));
const shortPeriod = `${String(year).slice(2)}${String(month).padStart(2, "0")}`;
const reportDate = (Date.UTC(year, month - 1, 1) - Date.UTC(1899, 11, 30)) / 86400000;

async function load(file) { return SpreadsheetFile.importXlsx(await FileBlob.load(file)); }
async function save(workbook, name) {
  const blob = await SpreadsheetFile.exportXlsx(workbook);
  await blob.save(path.join(outputDir, name));
  return name;
}
function write(sheet, range, values) { sheet.getRange(range).values = values; }
function vals(sheet, range) { return sheet.getRange(range).values; }
function letter(index) {
  let n = index, result = "";
  while (n > 0) { n--; result = String.fromCharCode(65 + n % 26) + result; n = Math.floor(n / 26); }
  return result;
}
function sameCities(rows, expected) {
  const names = rows.map(row => row[0]);
  if (names.length !== expected.length || names.some((name, i) => name !== expected[i])) {
    throw new Error(`地市顺序不一致：${names.join("、")}`);
  }
}
function fmt(value) { return Number(value).toFixed(2); }
function trend(value) { return value < 0 ? `下降${fmt(-value)}` : `上升${fmt(value)}`; }

await fs.mkdir(outputDir, { recursive: true });
const src = {};
for (const [key, filename] of Object.entries(sources)) {
  const book = await load(filename);
  const sheet = book.worksheets.getItemAt(0);
  src[key] = vals(sheet, key === "business" ? "A9:KF30" : "A6:BX27");
}
const sourceCities = src.full.map(row => row[0]);
if (sourceCities.length !== 22 || sourceCities[0] !== "全省") throw new Error("客户级源数据缺少全省和 21 地市");
for (const key of ["stock", "grow", "business"]) sameCities(src[key], sourceCities);

// 收保率对标：源数据写入计算模板；上月成表仅作可选版式。
const benchmarkTemplate = await load(refs.benchmarkTemplate);
write(benchmarkTemplate.worksheets.getItem("拍照客户_客户级"), "A6:BX27", src.full);
write(benchmarkTemplate.worksheets.getItem("拍照客户_业务级"), "A9:KF30", src.business);
write(benchmarkTemplate.worksheets.getItem("模板"), "B1", [[month]]);
benchmarkTemplate.recalculate();
const benchmarkResults = vals(benchmarkTemplate.worksheets.getItem("模板"), "C3:BO24");
const benchmarkFile = refs.benchmarkHistory || refs.benchmarkStyle;
const benchmark = benchmarkFile ? await load(benchmarkFile) : Workbook.create();
const benchmarkSheet = benchmarkFile ? benchmark.worksheets.getItemAt(0) : benchmark.worksheets.add("收保率对标分析");
if (!benchmarkFile) write(benchmarkSheet, "A1:BO24", vals(benchmarkTemplate.worksheets.getItem("模板"), "A1:BO24"));
benchmarkSheet.name = `${month}月政企收保率对标分析`;
write(benchmarkSheet, "B1", [[month]]);
write(benchmarkSheet, "A2", [[`${year}年${month}月`]]);
write(benchmarkSheet, "C3:BO24", benchmarkResults);
benchmarkSheet.getRange("C3:H24").setNumberFormat("0.00");
benchmarkSheet.getRange("I3:BO24").setNumberFormat("0.000");
benchmark.recalculate();
const benchmarkName = await save(benchmark, `${year}年${month}月收保率对标分析表.xlsx`);

// 指标跟踪：四份原样导出进入四个源页，七个结果页逐页进历史表。
const trackerTemplate = await load(refs.trackerTemplate);
for (const [key, sheetName, range] of [
  ["full", "全量客户（客户级）", "A6:BX27"],
  ["business", "全量客户（业务级）", "A9:KF30"],
  ["stock", "存存客户（客户级）", "A6:BX27"],
  ["grow", "存增客户（客户级）", "A6:BX27"],
]) write(trackerTemplate.worksheets.getItem(sheetName), range, src[key]);
const netSource = trackerTemplate.worksheets.getItem("按地市纯净收入");
for (let row = 4; row <= 25; row++) {
  // 导入的模板带上月缓存；重写整条依赖链才能可靠刷新纯净收入。
  netSource.getRange(`O${row}:R${row}`).clear({ applyTo: "contents" });
  netSource.getRange(`O${row}:R${row}`).formulas = [[
    `=M${row}-C${row}-E${row}-G${row}-I${row}`,
    `=N${row}-D${row}-F${row}-H${row}-J${row}`,
    `=VLOOKUP(B${row},'全量客户（客户级）'!$A$2:$BX$27,34,FALSE)/100`,
    `=P${row}/${month}*12/O${row}*100%`,
  ]];
}
trackerTemplate.recalculate();
if (!refs.trackerHistory || !positions) throw new Error("指标跟踪表缺少连续月份历史底稿");
const tracker = await load(refs.trackerHistory);
let cityData;

{
  const target = tracker.worksheets.getItem("整体指标");
  const previous = letter(positions.overallCol);
  const current = letter(positions.overallCol + 1);
  target.getRange(`${current}2:${current}20`).copyFrom(target.getRange(`${previous}2:${previous}20`), "all");
  write(target, `${current}2`, [[reportDate]]);
  write(target, `${current}14`, [[reportDate]]);
  const calculated = vals(trackerTemplate.worksheets.getItem("总体指标"), "B3:B20");
  write(target, `${current}3:${current}11`, calculated.slice(0, 9));
  write(target, `${current}15:${current}20`, calculated.slice(12, 18));
  target.getRange(`${current}2:${current}20`).format.columnWidth = 15;
}
{
  const target = tracker.worksheets.getItem("按用户结构");
  const oldStart = positions.structureEnd - 5;
  const newStart = positions.structureEnd + 1;
  target.getRange(`A${newStart}:J${newStart + 5}`).copyFrom(target.getRange(`A${oldStart}:J${positions.structureEnd}`), "all");
  write(target, `A${newStart}`, [[reportDate]]);
  for (const cell of ["C", "F", "I"]) write(target, `${cell}${newStart + 1}`, [[reportDate]]);
  write(target, `B${newStart + 2}:J${newStart + 4}`, vals(trackerTemplate.worksheets.getItem("按用户结构"), "B4:J6"));
  for (const range of [`A${newStart}:A${newStart + 1}`, `B${newStart}:D${newStart}`, `E${newStart}:G${newStart}`, `H${newStart}:J${newStart}`]) target.mergeCells(range);
}
{
  const target = tracker.worksheets.getItem("按地市");
  const calculated = vals(trackerTemplate.worksheets.getItem("按地市"), "A4:AJ25");
  sameCities(calculated.map(row => [row[1]]), [
    "全省", "深圳", "广州", "东莞", "佛山", "中山", "惠州", "江门", "珠海",
    "汕头", "揭阳", "潮州", "汕尾", "茂名", "湛江", "阳江", "云浮",
    "清远", "肇庆", "梅州", "韶关", "河源",
  ]);
  const oldHeader = positions.cityEnd - 24;
  const newHeader = positions.cityEnd + 3;
  const dataRow = newHeader + 3;
  target.getRange(`A${newHeader}:AK${newHeader + 24}`).copyFrom(target.getRange(`A${oldHeader}:AK${positions.cityEnd}`), "all");
  write(target, `A${newHeader}`, [[reportDate]]);
  write(target, `A${dataRow}:AJ${dataRow + 21}`, calculated);
  for (const range of [
    `A${newHeader}:B${newHeader + 2}`, `C${newHeader}:K${newHeader}`, `L${newHeader}:T${newHeader}`,
    `U${newHeader}:V${newHeader}`, `W${newHeader}:AJ${newHeader}`, `AK${newHeader}:AK${newHeader + 2}`,
    `C${newHeader + 1}:E${newHeader + 1}`, `F${newHeader + 1}:H${newHeader + 1}`,
    `I${newHeader + 1}:K${newHeader + 1}`, `L${newHeader + 1}:N${newHeader + 1}`,
    `O${newHeader + 1}:Q${newHeader + 1}`, `R${newHeader + 1}:T${newHeader + 1}`,
    `U${newHeader + 1}:U${newHeader + 2}`, `V${newHeader + 1}:V${newHeader + 2}`,
    `W${newHeader + 1}:AA${newHeader + 1}`, `AB${newHeader + 1}:AF${newHeader + 1}`,
    `AG${newHeader + 1}:AJ${newHeader + 1}`,
  ]) target.mergeCells(range);
  for (let row = dataRow; row <= dataRow + 21; row++) {
    const previous = row - 27;
    for (const [destination, input] of [["Y", "X"], ["AA", "Z"], ["AD", "AC"], ["AF", "AE"], ["AI", "AH"]]) {
      target.getRange(`${destination}${row}`).formulas = [[`=${input}${row}-${input}${previous}`]];
    }
    if (row === dataRow) write(target, `AK${row}`, [["—"]]);
    else target.getRange(`AK${row}`).formulas = [[`=RANK(L${row},$L$${dataRow + 1}:$L$${dataRow + 21},0)`]];
  }
  for (const [column, offset] of [
    ["C", 1], ["D", 1], ["E", 1], ["L", 1], ["M", 1], ["N", 1],
    ["U", 0], ["V", 1], ["W", 1], ["Z", 1], ["AB", 1],
    ["AE", 1], ["AG", 1], ["AH", 1],
  ]) {
    const range = target.getRange(`${column}${dataRow + offset}:${column}${dataRow + 21}`);
    range.conditionalFormats.add("top10", { rank: 5, bottom: false, format: { font: { color: "#92D050" } } });
    range.conditionalFormats.add("top10", { rank: 5, bottom: true, format: { font: { color: "#FF0000" } } });
  }
  config.cityDataRow = dataRow;
}
{
  const target = tracker.worksheets.getItem("按地市纯净收入");
  const calculated = vals(netSource, "M4:R25");
  write(target, "A1", [[`${month}月`]]);
  for (let i = 0; i < 22; i++) write(target, `C${i + 4}:H${i + 4}`, [calculated[i]]);
}
for (const name of ["按产品-客保", "按产品-收保"]) {
  const target = tracker.worksheets.getItem(name);
  const previous = letter(positions.productCol);
  const current = letter(positions.productCol + 1);
  target.getRange(`${current}2:${current}29`).copyFrom(target.getRange(`${previous}2:${previous}29`), "all");
  const calculated = vals(trackerTemplate.worksheets.getItem(name), "B2:B29");
  for (const row of [2, 8, 14, 20, 26]) calculated[row - 2][0] = Number(shortPeriod);
  write(target, `${current}2:${current}29`, calculated);
}
{
  const target = tracker.worksheets.getItem("各产品下用户结构");
  const oldStart = positions.compositionCol - 3;
  const newStart = positions.compositionCol + 1;
  const oldFirst = letter(oldStart), oldLast = letter(positions.compositionCol);
  const newFirst = letter(newStart), newLast = letter(newStart + 3);
  target.getRange(`${newFirst}1:${newLast}20`).copyFrom(target.getRange(`${oldFirst}1:${oldLast}20`), "all");
  const calculated = vals(trackerTemplate.worksheets.getItem("各产品下用户结构"), "M1:P20");
  for (const row of [1, 5, 9, 13, 17]) {
    calculated[row - 1][0] = `${String(year).slice(2)}年${month}月`;
    calculated[row - 1][2] = `${String(year).slice(2)}年${month}月结构`;
  }
  write(target, `${newFirst}1:${newLast}20`, calculated);
  for (const row of [1, 5, 9, 13, 17]) {
    target.mergeCells(`${letter(newStart + 2)}${row}:${newLast}${row}`);
    target.mergeCells(`${newFirst}${row + 1}:${newFirst}${row + 3}`);
    target.mergeCells(`${letter(newStart + 1)}${row + 1}:${letter(newStart + 1)}${row + 3}`);
  }
}
for (const [sheetName, count] of [["整体指标", 21], ["按地市", 18], ["按产品-客保", 15], ["按产品-收保", 16], ["各产品下用户结构", 122]]) {
  tracker.worksheets.getItem(sheetName).freezePanes.freezeColumns(count);
}
tracker.recalculate();
const trackerName = await save(tracker, `政企存量客户指标跟踪-${shortPeriod}.xlsx`);

const noticeFile = refs.noticeHistory || refs.noticeStyle;
const notice = noticeFile ? await load(noticeFile) : Workbook.create();
const noticeSheet = noticeFile ? notice.worksheets.getItemAt(0) : notice.worksheets.add("政企存量客户通报");
const citySheet = tracker.worksheets.getItem("按地市");
cityData = vals(citySheet, `A${config.cityDataRow}:AK${config.cityDataRow + 21}`);
if (!noticeFile) write(noticeSheet, "A4:AK6", vals(citySheet, "A1:AK3"));
write(noticeSheet, "C2", [[`收保客保月报（政企客户）-${month}月`]]);
write(noticeSheet, "A4", [[`${month}月`]]);
write(noticeSheet, "A7:AK28", cityData);
notice.recalculate();
const noticeName = await save(notice, `${year}年${month}月政企存量客户通报.xlsx`);

const province = cityData[0];
const local = cityData.slice(1).map(row => ({
  city: row[1], customer: row[2], customerYoY: row[3], customerMoM: row[4],
  revenue: row[11], revenueYoY: row[12], revenueMoM: row[13],
}));
const customerBottom = [...local].sort((a, b) => a.customer - b.customer).slice(0, 5);
const revenueBottom = [...local].sort((a, b) => a.revenue - b.revenue).slice(0, 5);
const min = (rows, key) => [...rows].sort((a, b) => a[key] - b[key])[0];
const customerYoY = min(customerBottom, "customerYoY");
const customerMoM = min(customerBottom, "customerMoM");
const revenueYoY = min(revenueBottom, "revenueYoY");
const revenueMoM = min(revenueBottom, "revenueMoM");
const text = [
  `${year}年${month}月份全省各地市存量经营指标通报数据见附件，请惠存。`, "",
  `${month}月全省客保率${fmt(province[2])}%，环比${trend(province[4])}个百分点；收保率${fmt(province[11])}%，环比${trend(province[13])}个百分点。`, "",
  `客保率后五为${customerBottom.map(row => row.city).join("、")}。其中，${customerYoY.city}同比${trend(customerYoY.customerYoY)}个百分点，${customerMoM.city}环比${trend(customerMoM.customerMoM)}个百分点，请重点关注。`, "",
  `收保率后五为${revenueBottom.map(row => row.city).join("、")}。其中，${revenueYoY.city}同比${trend(revenueYoY.revenueYoY)}个百分点，${revenueMoM.city}环比${trend(revenueMoM.revenueMoM)}个百分点，请重点关注。`, "",
].join("\n");
const textName = `通报文案-${year}年${month}月.txt`;
await fs.writeFile(path.join(outputDir, textName), text, "utf8");
console.log(JSON.stringify({ outputs: [benchmarkName, trackerName, noticeName, textName],
  cityDataRow: config.cityDataRow, province: [province[2], province[4], province[11], province[13]],
  customerBottom: customerBottom.map(row => row.city), revenueBottom: revenueBottom.map(row => row.city) }));
