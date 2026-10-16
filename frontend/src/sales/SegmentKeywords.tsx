/**
 * Ключи сбора чипами — одним списком в строку, в смете до запуска и в отчёте прогона.
 *
 * **Один список, а не два столбца** (волна 15: прогон размера №1 занимал на телефоне семь тысяч
 * пикселей, ключи — двумя столбцами списков). Взятый ключ — чипом с ✓, отсеянный — с ✗; слова
 * рядом — пометка взятого («похож на «…»» и любая другая — как их сказал сервер, без разбора),
 * причина отсева — приглушённо, и переносятся они внутри своей строки: на 390 px чипы уходят на
 * следующую строку, а страница не шире окна.
 *
 * **В смете у взятого — галочка** (`onToggle`): снятый ключ не покупается, рядом — «снят вами»,
 * и это не отсев — галочку можно вернуть. У отсеянного галочки нет. Чипы стоят на своих местах:
 * снятый не уезжает в другой список, и под курсор не подъезжает соседний (правило «Отбора»), а
 * строка чисел — под чипами, её длина их не двигает.
 *
 * **В отчёте — и что вышло с покупкой**: у взятого значок «выдача не пришла» (заплачено, а
 * провайдер не отдал) или «ничего не нашлось» (выдача пуста) — разные исходы, разными словами;
 * снятые на запуске — своим чипом.
 */

import { Badge, Checkbox, Stack, Text, VisuallyHidden } from '@mantine/core';
import { IconCheck, IconMinus, IconX } from '@tabler/icons-react';
import { useId } from 'react';
import type { CSSProperties } from 'react';

import type { SegmentRunDetail } from './segmentApi';
import { EXCLUDED_AT_START_COUNT, keysLine, LOST_MARK, runChips } from './segmentKeys';
import type { KeyChip, KeyState } from './segmentKeys';

/** Список чипов: в строку с переносом, без маркеров и отступа браузера. */
const LIST: CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  gap: '6px 12px',
  listStyle: 'none',
  margin: 0,
  padding: 0,
};

/** Ключ с его словами — строкой текста: длинная пометка переносится под чип, а не растит окно.
 *  Шрифт строки — мелкий, как у слов: перенос слов идёт плотно, а не строками основного текста. */
const ITEM: CSSProperties = {
  maxWidth: '100%',
  minWidth: 0,
  overflowWrap: 'anywhere',
  fontSize: 'var(--mantine-font-size-xs)',
  lineHeight: 1.5,
};

/** Подложка чипа по состоянию: взятый — тоном акцента, снятый — без подложки, отсеянный — серым.
 *  Текст — чернилами темы: подложки — подсветка, а не цвет текста. У снятого чернила полные:
 *  тихие на прозрачном чипе дали 4,05 : 1 при норме 4,5 (замер стенда), а что он снят, говорят
 *  пустая галочка и слова «снят вами». Рамки у снятого нет: пунктир у самого текста мерился
 *  вместе с буквами (4,50 и 3,74 при норме 4,5). Поля и рамка (прозрачная) — у всех одни: снятие
 *  не меняет размер чипа, и соседние не сдвигаются. */
const FILL: Record<KeyState, string> = {
  taken: 'var(--mantine-color-lagoon-light)',
  excluded: 'transparent',
  rejected: 'var(--mantine-color-gray-light)',
};

function pill(state: KeyState): CSSProperties {
  return {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    maxWidth: '100%',
    verticalAlign: 'middle',
    padding: '1px 10px 1px 7px',
    borderRadius: 999,
    background: FILL[state],
    border: '1px solid transparent',
    color: state === 'rejected' ? 'var(--ink-soft)' : 'var(--ink)',
  };
}

/** Как состояние слышит программа чтения с экрана: значки ✓ и ✗ ей не видны. */
const STATE_WORDS: Record<KeyState, string> = {
  taken: 'взят',
  excluded: 'снят',
  rejected: 'отсеян',
};

const ICONS: Record<KeyState, typeof IconCheck> = {
  taken: IconCheck,
  excluded: IconMinus,
  rejected: IconX,
};

function StaticChip({ chip }: { chip: KeyChip }) {
  const Icon = ICONS[chip.state];
  return (
    <span className="keyChip" data-state={chip.state} style={pill(chip.state)}>
      <Icon size={14} stroke={2} aria-hidden style={{ flexShrink: 0 }} />
      <VisuallyHidden>{STATE_WORDS[chip.state]}: </VisuallyHidden>
      <Text span size="sm" c="inherit">
        {chip.phrase}
      </Text>
    </span>
  );
}

function ToggleChip({
  chip,
  line,
  words,
  onToggle,
}: {
  chip: KeyChip;
  line: number;
  words: string | undefined;
  onToggle: (line: number, taken: boolean) => void;
}) {
  return (
    <Checkbox
      size="xs"
      label={chip.phrase}
      checked={chip.state === 'taken'}
      onChange={(event) => onToggle(line, event.currentTarget.checked)}
      aria-describedby={words}
      className="keyChip"
      wrapperProps={{ 'data-state': chip.state }}
      styles={{
        root: { ...pill(chip.state), padding: '2px 10px 2px 7px' },
        body: { alignItems: 'center' },
        labelWrapper: { minWidth: 0 },
        label: {
          paddingInlineStart: 6,
          fontSize: 'var(--mantine-font-size-sm)',
          color: 'inherit',
          cursor: 'pointer',
          overflowWrap: 'anywhere',
        },
      }}
    />
  );
}

function Chip({
  chip,
  onToggle,
}: {
  chip: KeyChip;
  onToggle: ((line: number, taken: boolean) => void) | undefined;
}) {
  const wordsId = useId();
  const toggles = onToggle !== undefined && chip.line !== undefined && chip.state !== 'rejected';
  return (
    <li style={ITEM}>
      {toggles && chip.line !== undefined ? (
        <ToggleChip
          chip={chip}
          line={chip.line}
          words={chip.words === undefined ? undefined : wordsId}
          onToggle={onToggle}
        />
      ) : (
        <StaticChip chip={chip} />
      )}
      {/* Между чипом, значками и словами — пробелы, а не отступы: строка читается фразой и
          программой чтения с экрана, а перенос идёт по ним. */}
      {(chip.marks ?? []).map((mark) => (
        <span key={mark}>
          {' '}
          <Badge
            size="sm"
            variant="light"
            color={mark === LOST_MARK ? 'yellow' : 'gray'}
            style={{ verticalAlign: 'middle' }}
          >
            {mark}
          </Badge>
        </span>
      ))}
      {chip.words !== undefined && (
        <>
          {' '}
          <Text span id={wordsId} size="xs" c="dimmed" className="keyWords">
            {chip.words}
          </Text>
        </>
      )}
    </li>
  );
}

interface ChipsProps {
  /** Подпись над чипами — она же имя списка. */
  label: string;
  chips: KeyChip[];
  /** Как назван снятый в строке чисел: «снято вами» в смете, «снято на запуске» в отчёте. */
  excludedWords: string;
  /** Снять ключ или вернуть ему галочку — у взятых и снятых ключей сметы. */
  onToggle?: (line: number, taken: boolean) => void;
  /** Что сказать под строкой чисел: как работает галочка, идёт ли пересчёт. */
  note?: string;
}

/** Ключи чипами: подпись, чипы в строку, под ними — числа. */
export function KeywordChips({ label, chips, excludedWords, onToggle, note }: ChipsProps) {
  return (
    <Stack gap={6}>
      <Text size="sm" fw={500}>
        {label}
      </Text>
      <ul aria-label={label} style={LIST}>
        {/* Ключ элемента — без состояния взятого: снятие галочки не пересоздаёт чип, и фокус с
            клавиатуры остаётся на нём. Отсеянный — своим ключом: повтор, вписанный в том же виде,
            совпадает со взятым словами. */}
        {chips.map((chip) => (
          <Chip
            key={`${chip.state === 'rejected' ? 'rejected' : 'chosen'}:${chip.phrase}`}
            chip={chip}
            onToggle={onToggle}
          />
        ))}
      </ul>
      <Text size="xs" c="dimmed" aria-live="polite" className="keyCounts">
        {keysLine(chips, excludedWords)}
        {note === undefined ? '' : ` ${note}`}
      </Text>
    </Stack>
  );
}

/** Ключи прогона: как их видел человек на запуске — и что вышло с покупкой выдачи. */
export function SegmentKeywords({ detail }: { detail: SegmentRunDetail }) {
  const found = detail.spend?.keywords;
  if (found === undefined) return null;
  return (
    <KeywordChips
      label="Ключи прогона"
      chips={runChips(found)}
      excludedWords={EXCLUDED_AT_START_COUNT}
    />
  );
}
