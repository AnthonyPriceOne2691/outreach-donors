/**
 * «Загрузить списком» — доноры с ценами файлом, рядом с «Завести донора вручную» (29.10.2026).
 *
 * Тест-группа №2: «нужен инструмент массового заноса доноров — до 10 000 за выгрузку». До этого
 * донора с ценой заводили по одному. Файл — CSV или Excel (.xlsx) с колонками шаблона: домен,
 * цена, валюта, откуда цена. Каждую строку сервер судит правилом формы (`donors/bulk_entry.py`),
 * экран показывает его отчёт как есть.
 *
 * **Сначала отчёт, потом запись.** Выбрал файл — сервер разобрал его без записи: сколько заведёт,
 * у кого обновит цену и кого пропустит, с номером строки и причиной словами формы. «Завести»
 * пишет и проверяет всё заново. Длинный список проблем — первые строки в окне, полный — файлом.
 *
 * **Обход — галочкой, через очередь загрузки** (`CrawlBacklog`): десять тысяч обходов вперёд
 * не встают в очередь обходов разом, сервис отдаёт их обходчикам по мере освобождения. Окно
 * называет срок при нынешних обходчиках — по времени последних обходов.
 *
 * **Какой файл подойдёт — у «i»** (доработка по слову Anthony 29.10.2026): у кнопки и у поля
 * файла — вид, колонки, валюта по умолчанию, пределы, пример и «Скачать шаблон». Файл не того
 * вида, пустой или больше предела на сервер не уходит — причина под полем (`bulkFile.ts`).
 * Отказ сервера — плашкой в окне, а не уведомлением: файл надо поправить, а уведомление
 * закрыли бы, не дочитав.
 */

import {
  Alert,
  Anchor,
  Button,
  Checkbox,
  Code,
  FileInput,
  Group,
  Modal,
  Stack,
  Table,
  Text,
} from '@mantine/core';
import { IconUpload } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { BulkReport, BulkRow } from '../api/bulkDonors';
import { downloadDonorTemplate, enterDonorList, previewDonorList } from '../api/bulkDonors';
import { refusalOf } from '../api/client';
import { saveFile } from '../api/donors';
import { InfoHint } from '../components/InfoHint';
import { formatNumber, plural } from '../format';
import { notify, refuse, settle } from '../notices';
import { FILE_LIMIT_MB, ROW_LIMIT, fileRefusal, refusalWords } from './bulkFile';
import { donorsWord, etaWords } from './CrawlBacklog';

/** Строк с причиной — в окне не больше; полный отчёт — файлом. */
const SHOWN = 200;
const TEMPLATE_NAME = 'доноры-шаблон.csv';
const TEMPLATE_ACTION = 'donors-import-template';
/** Пример в подсказке: заголовок шаблона и две строки — у второй валюта пустая. */
const EXAMPLE = [
  'домен,цена,валюта,откуда цена',
  'example.com,150,USD,прайс агентства',
  'example.org,90,,сайт донора',
].join('\n');
const OUTCOME: Record<BulkRow['outcome'], string> = {
  created: 'заведён',
  priced: 'цена обновлена',
  skipped: 'пропущен',
};

/** Что выйдет или вышло — одной фразой. */
export function reportWords(report: BulkReport, done: boolean): string {
  const made = done ? 'заведено' : 'заведём';
  const priced = done ? 'цена обновлена у' : 'цена обновится у';
  const skipped = done ? 'пропущено' : 'пропустим';
  return (
    `Новых доноров ${made}: ${formatNumber(report.created)}, ${priced} ` +
    `${formatNumber(report.priced)}, ${skipped} ${formatNumber(report.skipped)} ` +
    `${plural(report.skipped, 'строку', 'строки', 'строк')}.`
  );
}

/** Отчёт CSV для Excel: метка BOM, поля в кавычках по правилам CSV. */
export function reportCsv(rows: BulkRow[]): string {
  const cell = (value: string | number | null) => {
    const text = value === null ? '' : String(value);
    return /[",\n;]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const head = ['строка', 'домен в файле', 'итог', 'домен', 'почему'];
  const lines = rows.map((row) =>
    [row.line, row.given, OUTCOME[row.outcome], row.host, row.reason].map(cell).join(','),
  );
  return `\uFEFF${[head.join(','), ...lines].join('\n')}\n`;
}

function Problems({ report }: { report: BulkReport }) {
  const noted = report.rows.filter((row) => row.reason !== null);
  if (noted.length === 0) return null;
  const save = () =>
    saveFile(
      {
        blob: new Blob([reportCsv(report.rows)], { type: 'text/csv' }),
        filename: null,
        headers: new Headers(),
      },
      `отчёт — ${report.file || 'доноры'}.csv`,
    );
  return (
    <Stack gap="xs">
      <Table.ScrollContainer minWidth={560} mah={320}>
        <Table striped withTableBorder fz="sm" aria-label="Строки с причиной">
          <Table.Thead>
            <Table.Tr>
              <Table.Th w="5rem">Строка</Table.Th>
              <Table.Th w="14rem">Домен в файле</Table.Th>
              <Table.Th>Почему</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {noted.slice(0, SHOWN).map((row) => (
              <Table.Tr key={row.line}>
                <Table.Td>{formatNumber(row.line)}</Table.Td>
                <Table.Td style={{ overflowWrap: 'anywhere' }}>{row.given || '—'}</Table.Td>
                <Table.Td>{row.reason}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <Group justify="space-between" gap="xs">
        <Text size="xs" c="dimmed">
          {noted.length > SHOWN
            ? `Показаны первые ${formatNumber(SHOWN)} из ${formatNumber(noted.length)}.`
            : `Строк с причиной: ${formatNumber(noted.length)}.`}
        </Text>
        <Button variant="subtle" size="xs" onClick={save}>
          Скачать отчёт
        </Button>
      </Group>
    </Stack>
  );
}

/** Отказ сервера на файл `name` — плашкой в окне, словами: что не совпало. */
function Refused({ error, title, name }: { error: unknown; title: string; name: string }) {
  if (error === null || error === undefined) return null;
  return (
    <Alert color="red" title={title}>
      {refusalWords(error, name)}
    </Alert>
  );
}

/** Шаблон загрузки — из подсказки и из окна. Не скачался — уведомлением: окно он не меняет. */
function useTemplate() {
  return useMutation({
    mutationFn: downloadDonorTemplate,
    onSuccess: (got) => {
      settle(TEMPLATE_ACTION);
      saveFile(got, TEMPLATE_NAME);
    },
    onError: (error) =>
      refuse(TEMPLATE_ACTION, { title: 'Шаблон не скачан', message: refusalOf(error) }),
  });
}

/** Какой файл подойдёт — у «Загрузить списком» и у поля файла: те же правила, что у
 *  читателя на сервере (`donors/bulk_file.py`). */
function FormatHint() {
  const template = useTemplate();
  return (
    <InfoHint name="Какой файл подойдёт" width={340} interactive>
      <Stack gap={6}>
        <Text size="sm">Таблица CSV или Excel (XLSX) — из Excel читается первый лист.</Text>
        <Text size="sm">
          Колонки — по заголовку или, без заголовка, по порядку: домен, цена, валюта, откуда цена.
          Серая цена и способы оплаты — колонками с такими заголовками, если они есть.
        </Text>
        <Text size="sm">
          Пустая валюта — USD. До {formatNumber(ROW_LIMIT)} строк и {FILE_LIMIT_MB} МБ.
        </Text>
        <Code block className="glassQuiet" fz="xs">
          {EXAMPLE}
        </Code>
        <Anchor component="button" type="button" size="sm" onClick={() => template.mutate()}>
          Скачать шаблон
        </Anchor>
      </Stack>
    </InfoHint>
  );
}

/** Что выйдет с файлом: итог, строки с причиной и галочка обхода. */
function Outcome({
  report,
  crawl,
  onCrawl,
}: {
  report: BulkReport;
  crawl: boolean;
  onCrawl: (crawl: boolean) => void;
}) {
  return (
    <>
      <Text size="sm">{reportWords(report, false)}</Text>
      <Problems report={report} />
      <Checkbox
        checked={crawl && report.crawlable > 0}
        disabled={report.crawlable === 0}
        onChange={(event) => onCrawl(event.currentTarget.checked)}
        label={`Поставить в обход: ${donorsWord(report.crawlable)}`}
        description={`Через очередь загрузки — ${etaWords(report.backlog)}, считая уже ждущих; обход, запущенный кнопкой, идёт первым.`}
      />
    </>
  );
}

export function BulkDonors({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [crawl, setCrawl] = useState(true);
  const preview = useMutation({ mutationFn: (chosen: File) => previewDonorList(chosen) });
  const template = useTemplate();
  const enter = useMutation({
    mutationFn: (chosen: File) => enterDonorList(chosen, crawl),
    onSuccess: (report) => {
      const queued = report.queued > 0 ? ` В очередь обхода: ${donorsWord(report.queued)}.` : '';
      notify({ message: `${reportWords(report, true)}${queued}`, color: 'green' });
      onDone();
    },
  });
  const report = preview.data;
  const writes = report === undefined ? 0 : report.created + report.priced;
  const name = file?.name ?? '';
  const choose = (chosen: File | null) => {
    setFile(chosen);
    enter.reset();
    preview.reset();
    if (chosen !== null && fileRefusal(chosen) === null) preview.mutate(chosen);
  };
  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed" maw={720}>
        Таблица CSV или Excel (.xlsx), до {formatNumber(ROW_LIMIT)} строк и {FILE_LIMIT_MB} МБ:
        домен, цена, валюта и откуда цена — как в шаблоне. Каждую строку сервис проверит тем же
        правилом, что «Завести донора вручную», и до записи покажет, кого заведёт, а кого нет и
        почему.
      </Text>
      <FileInput
        labelProps={{ labelElement: 'div' }}
        label={
          <Group component="span" gap={4} wrap="nowrap">
            Файл
            <FormatHint />
          </Group>
        }
        aria-label="Файл"
        placeholder="выберите файл"
        accept=".csv,.xlsx,text/csv"
        leftSection={<IconUpload size={16} />}
        value={file}
        onChange={choose}
        error={file === null ? null : fileRefusal(file)}
        clearable
      />
      {preview.isPending ? <Text size="sm">Проверяем строки…</Text> : null}
      <Refused error={preview.error} title="Файл не прочитан" name={name} />
      <Refused error={enter.error} title="Доноры не заведены" name={name} />
      {report ? <Outcome report={report} crawl={crawl} onCrawl={setCrawl} /> : null}
      <Group justify="space-between" gap="sm">
        <Button variant="subtle" loading={template.isPending} onClick={() => template.mutate()}>
          Скачать шаблон
        </Button>
        <Group gap="sm">
          <Button variant="default" onClick={onCancel}>
            Отмена
          </Button>
          <Button
            disabled={file === null || writes === 0 || preview.isPending}
            loading={enter.isPending}
            onClick={() => file !== null && enter.mutate(file)}
          >
            Завести · {formatNumber(writes)}
          </Button>
        </Group>
      </Group>
    </Stack>
  );
}

/**
 * Кнопка рядом с «Завести донора вручную» и окно загрузки — одним куском: панель ставит его
 * в шапку одной строкой, и перенос панели (вкладка, страницы на тысячи доноров) его не ломает.
 * После загрузки перечитывается всё: меняются таблица доноров, панель обхода, очередь
 * загрузки и числа меню, и список ключей здесь разошёлся бы с ними на первой правке.
 */
export function BulkDonorsButton() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Group gap={4} wrap="nowrap">
        <Button
          variant="default"
          className="press"
          aria-haspopup="dialog"
          onClick={() => setOpen(true)}
        >
          Загрузить списком
        </Button>
        <FormatHint />
      </Group>
      <Modal
        opened={open}
        onClose={() => setOpen(false)}
        title="Загрузить доноров списком"
        size="xl"
      >
        <BulkDonors
          onDone={() => {
            setOpen(false);
            void queryClient.invalidateQueries();
          }}
          onCancel={() => setOpen(false)}
        />
      </Modal>
    </>
  );
}
