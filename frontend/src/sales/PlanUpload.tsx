/**
 * «Загрузить план работ» на вкладке «Гипотезы»: книга Excel — лист за гипотезой, превью и смета до
 * сохранения.
 *
 * **Сначала превью, потом план.** Выбрал книгу — сервер разобрал её без записи: по листу — гипотеза,
 * рынки, язык, цель, глубина, ключи, ошибки и смета «от — до»; внизу — смета плана и почему план не
 * сохранить. Режим, общая цель и потолки — поля окна: смета считается по ним, а сохраняется только
 * то, что человек видел (отпечаток превью). Поменял поле после проверки — «Проверить заново», и
 * «Сохранить план» закрыт, пока превью не совпадёт с полями.
 *
 * **Отказ файла — у файла** (`docs/UI_RULES.md`): не .xlsx, пустой или больше 7 МБ — под полем и без
 * запроса; отказ сервера — плашкой в окне словами сервера, страница 413 прокси — словами предела.
 */

import {
  Alert,
  Button,
  FileInput,
  Group,
  Modal,
  NumberInput,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
} from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { PlanChoice, PlanMode, PlanPreview } from '../api/salesPlans';
import { previewPlan, savePlan } from '../api/salesPlans';
import { refusalWords } from '../advertisers/bulkFile';
import { InfoHint } from '../components/InfoHint';
import { formatNumber, plural } from '../format';
import { notify } from '../notices';
import { OneScrollForm } from './OneScrollForm';
import { PlanPreviewView } from './PlanPreviewView';
import { PLANS_QUERY_KEY, PlansList } from './PlansList';

/** Предел книги, МБ: тот же, что у разбора на сервере (`sales/plan_file.py`). */
export const PLAN_LIMIT_MB = 7;
const SHEETS = 10;
const SHEET_KEYS = 30_000;
const START: PlanChoice = { mode: 'economy', goal: null, capUsd: null, hypothesisCapUsd: null };
const MODES: { value: PlanMode; label: string }[] = [
  { value: 'economy', label: 'Экономный' },
  { value: 'fast', label: 'Быстрый' },
];

/** Что не так с книгой ещё до отправки; `null` — можно отправлять. Слова — как у сервера. */
export function planFileRefusal(file: File): string | null {
  const name = file.name.toLowerCase();
  if (name.endsWith('.xls')) return 'Старый формат Excel (.xls) — сохраните книгу как .xlsx.';
  if (!name.endsWith('.xlsx')) {
    return `Файл «${file.name}» — не книга Excel (.xlsx): план читается только из .xlsx.`;
  }
  if (file.size === 0) return `Файл «${file.name}» пуст.`;
  if (file.size > PLAN_LIMIT_MB * 2 ** 20) {
    return `Файл «${file.name}» больше ${PLAN_LIMIT_MB} МБ — разделите план на две книги.`;
  }
  return null;
}

/** Ключ того, что проверено: книга и поля окна. Разошёлся — превью устарело. */
function checkKey(file: File | null, choice: PlanChoice): string {
  return JSON.stringify([file?.name, file?.size, file?.lastModified, choice]);
}

function BookHint() {
  return (
    <InfoHint name="Какая книга подойдёт" width={340}>
      Книга .xlsx: лист — гипотеза. Сверху — карточка: «Название», «Кому пишем», «Где ищем», «Язык
      писем» и необязательные поля; ниже строка «Ключи» и ключи по одному в строке. Лист «Как
      заполнять» не читается. До {SHEETS} листов по {formatNumber(SHEET_KEYS)} ключей и{' '}
      {PLAN_LIMIT_MB} МБ.
    </InfoHint>
  );
}

/** Число поля; пусто — `null`. */
function numberOf(value: string | number): number | null {
  return typeof value === 'number' ? value : null;
}

/** Поля окна: режим, общая цель, потолки. */
function ChoiceFields({
  choice,
  onChoice,
}: {
  choice: PlanChoice;
  onChoice: (choice: PlanChoice) => void;
}) {
  return (
    <Stack gap="xs">
      <Group gap={6} wrap="nowrap">
        <SegmentedControl
          aria-label="Режим"
          data={MODES}
          value={choice.mode}
          onChange={(mode) => onChoice({ ...choice, mode: mode as PlanMode })}
        />
        <InfoHint name="Режим" width={300}>
          Экономный — выдача в отложенной очереди: дешевле, минуты на пачку ключей. Быстрый — сразу,
          но дороже.
        </InfoHint>
      </Group>
      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="sm">
        <NumberInput
          label="Общая цель, сайтов"
          description="для листов без своей"
          min={1}
          allowDecimal={false}
          thousandSeparator=" "
          value={choice.goal ?? ''}
          onChange={(value) => onChoice({ ...choice, goal: numberOf(value) })}
        />
        <NumberInput
          label="Потолок плана, $"
          min={0}
          decimalScale={2}
          value={choice.capUsd ?? ''}
          onChange={(value) => onChoice({ ...choice, capUsd: numberOf(value) })}
        />
        <NumberInput
          label="Потолок гипотезы, $"
          min={0}
          decimalScale={2}
          value={choice.hypothesisCapUsd ?? ''}
          onChange={(value) => onChoice({ ...choice, hypothesisCapUsd: numberOf(value) })}
        />
      </SimpleGrid>
    </Stack>
  );
}

/** Отказ сервера — плашкой в окне, словами сервера; нет отказа — ничего. */
function ServerRefusal(props: { error: unknown; title: string; file: string }) {
  return props.error ? (
    <Alert color="red" title={props.title}>
      {refusalWords(props.error, props.file)}
    </Alert>
  ) : null;
}

/** Книга, поля окна и превью: что проверено и устарело ли оно. */
function usePlanCheck() {
  const [file, setFile] = useState<File | null>(null);
  const [choice, setChoice] = useState<PlanChoice>(START);
  const [checked, setChecked] = useState<string | null>(null);
  const preview = useMutation({
    mutationFn: ({ book, asked }: { book: File; asked: PlanChoice }) => previewPlan(book, asked),
    onSuccess: (_found, { book, asked }) => setChecked(checkKey(book, asked)),
  });
  const check = (book: File | null, asked: PlanChoice) => {
    if (book !== null && planFileRefusal(book) === null) preview.mutate({ book, asked });
  };
  const choose = (book: File | null) => {
    setFile(book);
    preview.reset();
    setChecked(null);
    check(book, choice);
  };
  const stale = preview.data !== undefined && checked !== checkKey(file, choice);
  return { file, choice, setChoice, preview, check, choose, stale };
}

/** Сохранение по отпечатку превью: уведомление, перечитанные гипотезы и планы. */
function usePlanSave(choice: PlanChoice, onDone: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ book, found }: { book: File; found: PlanPreview }) =>
      savePlan(book, choice, found.digest),
    onSuccess: (plan) => {
      const count = plan.hypotheses.length;
      const word = plural(count, 'гипотеза', 'гипотезы', 'гипотез');
      notify({
        message: `План «${plan.name}» сохранён: ${formatNumber(count)} ${word}.`,
        color: 'green',
      });
      void queryClient.invalidateQueries({ queryKey: ['sales'] });
      void queryClient.invalidateQueries({ queryKey: PLANS_QUERY_KEY });
      onDone();
    },
  });
}

/** Можно ли сохранять: книга годна, превью есть, свежее, без отказов и не идёт. */
function savable(file: File | null, found: PlanPreview | undefined, busy: boolean): boolean {
  if (file === null || found === undefined || busy) return false;
  return planFileRefusal(file) === null && found.refusals.length === 0;
}

export function PlanUpload({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const { file, choice, setChoice, preview, check, choose, stale } = usePlanCheck();
  const save = usePlanSave(choice, onDone);
  const found = preview.data;
  const name = file?.name ?? '';
  const open = savable(file, found, preview.isPending) && !stale;
  const send = () => {
    if (file !== null && found !== undefined) save.mutate({ book: file, found });
  };
  const refused = save.error ?? preview.error;
  const title = save.error ? 'План не сохранён' : 'Книга не прочитана';
  return (
    <OneScrollForm
      onSubmit={(event) => {
        event.preventDefault();
        check(file, choice);
      }}
      top={refused ? <ServerRefusal error={refused} title={title} file={name} /> : null}
      footer={
        <Group justify="flex-end" gap="sm">
          <Button variant="default" onClick={onCancel}>
            Отмена
          </Button>
          <Button
            variant="default"
            disabled={file === null || preview.isPending}
            onClick={() => {
              save.reset();
              check(file, choice);
            }}
          >
            {found === undefined ? 'Проверить' : 'Проверить заново'}
          </Button>
          <Button disabled={!open} loading={save.isPending} onClick={send}>
            Сохранить план
          </Button>
        </Group>
      }
    >
      <FileInput
        labelProps={{ labelElement: 'div' }}
        label={
          <Group component="span" gap={4} wrap="nowrap">
            Книга
            <BookHint />
          </Group>
        }
        aria-label="Книга"
        placeholder="выберите книгу .xlsx"
        accept=".xlsx"
        value={file}
        onChange={choose}
        error={file === null ? null : planFileRefusal(file)}
        clearable
      />
      <ChoiceFields choice={choice} onChoice={setChoice} />
      <PreviewPart preview={preview} stale={stale} />
    </OneScrollForm>
  );
}

/** Превью или «проверяем»; поля изменились после проверки — словами. */
function PreviewPart({
  preview,
  stale,
}: {
  preview: { data: PlanPreview | undefined; isPending: boolean };
  stale: boolean;
}) {
  if (preview.isPending) return <Text size="sm">Проверяем книгу…</Text>;
  if (preview.data === undefined) return null;
  return (
    <>
      <PlanPreviewView preview={preview.data} />
      {stale ? (
        <Text size="sm" c="dimmed">
          Поля изменились после проверки — проверьте заново.
        </Text>
      ) : null}
    </>
  );
}

/** Кнопки рядом с «Новая гипотеза»: окно загрузки плана и список планов. Сохранил план — окно
 *  загрузки закрывается, и открывается список: новый план в нём первым. */
export function PlanUploadButton() {
  const [open, setOpen] = useState<'upload' | 'plans' | null>(null);
  const close = () => setOpen(null);
  return (
    <>
      <Group gap={4} wrap="nowrap">
        <Button
          variant="default"
          className="press"
          aria-haspopup="dialog"
          onClick={() => setOpen('upload')}
        >
          Загрузить план работ
        </Button>
        <BookHint />
      </Group>
      <Button variant="subtle" aria-haspopup="dialog" onClick={() => setOpen('plans')}>
        Планы работ
      </Button>
      <Modal
        opened={open === 'upload'}
        onClose={close}
        title="Загрузить план работ"
        size="xl"
        classNames={{ content: 'oneScroll' }}
      >
        <PlanUpload onDone={() => setOpen('plans')} onCancel={close} />
      </Modal>
      <Modal
        opened={open === 'plans'}
        onClose={close}
        title="Планы работ"
        size="xl"
        classNames={{ content: 'oneScroll' }}
      >
        <div className="oneScrollArea scrollSlim">{open === 'plans' ? <PlansList /> : null}</div>
      </Modal>
    </>
  );
}
