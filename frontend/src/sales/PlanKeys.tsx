/**
 * «Добавить ключи» гипотезе плана — текстом, по ключу в строке, или книгой .xlsx одним листом:
 * ключи в колонке A, под строкой «Ключи», если она есть (лист плана годится как есть).
 *
 * Сервер чистит ключи так же, как ключи книги плана, кладёт новые на каждый рынок гипотезы и
 * снимает «нужны ключи»; гипотеза, стоявшая из-за них, снова собирает. Что вышло — уведомлением:
 * сколько новых, сколько уже было, сколько отсеяно. Отказ — словами сервера над полем.
 */

import { FileInput, SegmentedControl, Textarea } from '@mantine/core';
import { useState } from 'react';

import { formatNumber, plural } from '../format';
import { notify } from '../notices';
import { addKeys, usePlanAction } from './planApi';
import type { PlanKeysAdded, PlanRow } from './planApi';
import { PlanDialog } from './PlanDialog';

type Way = 'text' | 'file';

const WAYS: { value: Way; label: string }[] = [
  { value: 'text', label: 'Текстом' },
  { value: 'file', label: 'Книгой .xlsx' },
];

/** «Добавлено 120 ключей · уже были 3 · отсеяно 1». */
export function addedWords(found: PlanKeysAdded): string {
  const keys = plural(found.added, 'ключ', 'ключа', 'ключей');
  const parts = [`Добавлено ${formatNumber(found.added)} ${keys}`];
  if (found.known > 0) parts.push(`уже были ${formatNumber(found.known)}`);
  if (found.rejected > 0) parts.push(`отсеяно ${formatNumber(found.rejected)}`);
  return parts.join(' · ');
}

interface Props {
  row: PlanRow;
  onClose: () => void;
}

/** Что уйдёт на сервер: текст или книга; не вписано — `null`. */
function keysOf(
  way: Way,
  text: string,
  file: File | null,
): { text: string } | { file: File } | null {
  if (way === 'file') return file === null ? null : { file };
  return text.trim() === '' ? null : { text };
}

export function PlanKeys({ row, onClose }: Props) {
  const [way, setWay] = useState<Way>('text');
  const [text, setText] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const send = usePlanAction(
    (keys: { text: string } | { file: File }) => addKeys(row.id, keys),
    (found) => {
      notify({ message: `«${row.name}»: ${addedWords(found)}`, color: 'green' });
      onClose();
    },
  );
  const keys = keysOf(way, text, file);
  return (
    <PlanDialog
      title={`Добавить ключи: «${row.name}»`}
      refused="Ключи не добавлены"
      error={send.error}
      busy={send.isPending}
      ready={keys !== null}
      action="Добавить"
      onSubmit={() => {
        if (keys !== null) send.mutate(keys);
      }}
      onClose={onClose}
    >
      <SegmentedControl
        aria-label="Как добавить ключи"
        value={way}
        data={WAYS}
        onChange={(picked) => setWay(picked === 'file' ? 'file' : 'text')}
        style={{ alignSelf: 'flex-start' }}
      />
      {way === 'text' ? (
        <Textarea
          label="Ключи"
          description="По ключу в строке. Повторы и ключи, которые уже есть у гипотезы, не добавятся"
          autosize
          minRows={6}
          maxRows={14}
          value={text}
          data-autofocus
          onChange={(event) => setText(event.currentTarget.value)}
        />
      ) : (
        <FileInput
          label="Книга"
          description="Один лист, ключи в колонке A — под строкой «Ключи», если она есть"
          placeholder="выберите книгу .xlsx"
          accept=".xlsx"
          value={file}
          onChange={setFile}
          clearable
        />
      )}
    </PlanDialog>
  );
}
