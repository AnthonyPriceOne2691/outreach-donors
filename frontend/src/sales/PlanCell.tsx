/**
 * Колонка «План работ» таблицы гипотез — только у гипотез плана: этап словами, «найдено N из цели»
 * с полосой, расход из потолка гипотезы, проблемы строками, кнопки и ссылки.
 *
 * **Кнопки — по этапу.** Собирает или ждёт очереди — «Остановить»; остановлена — «Продолжить»
 * (сервер продолжит тот же прогон: выдача второй раз не покупается). Набравшую цель и готовую не
 * останавливают — прогонов у неё уже нет. «Качество сайтов» и «Собрать ещё» — всегда, «Добавить
 * ключи» — при «нужны ключи». Отказ сервера — словами под кнопками, а не между ними (стенд 17.11:
 * отказ «Остановить» вставал в ряд кнопок и сдвигал остальные вниз).
 *
 * **Окно кнопки** монтируется уже открытым и снимается целиком, и Mantine фокус не возвращает: он
 * запоминает кнопку только при смене `opened`. Возвращает ячейка — на кнопку, которая окно открыла
 * (стенд 17.11: после Esc фокус уходил на страницу, место в таблице терялось).
 *
 * **Слова — от левого края и рвутся где угодно:** проблемы и отказы — слова сервера, длинное слово
 * без пробела вылезало за край таблицы (стенд 17.11, 176 px).
 *
 * **Полоса «найдено N из цели» — дорожкой `Meter`** (`.meter` в `glass.css`: светлая ступень
 * акцента, видна и при 0 %; серая дорожка Mantine в тёмной теме давала заливке 1,70 : 1 при норме 3,
 * а при 0 % полосы не было видно — стенд 17.11), **но не самим `Meter`**: его янтарь с 80 % и роза у
 * предела — про расход из лимита, а дойти до цели — хорошо, и роза у готовой гипотезы читалась бы
 * отказом. Заливка — по теме: на свету акцент к своей дорожке — 2,42 : 1, глубокая ступень
 * (`--accent-deep`) — выше 3; в темноте акцент — 4,25 (стенд 18.11). Значение и имя для читалки —
 * у полосы: «имя»: найдено N из M.
 *
 * **Имена кнопок и ссылок для читалки — с именем гипотезы** («Качество сайтов гипотезы «…»»): в
 * таблице их по набору на строку, и пять одинаковых «Собрать ещё» подряд не различить без глаз.
 * Видимое слово — начало имени (правило «label in name»: голосом зовут то, что видно).
 *
 * **На телефоне** (уже 48em) первая строка ячейки — имя гипотезы: таблица там уезжает вбок без
 * закреплённого имени, и после прокрутки не понять, чья строка. Читалке она не нужна — имя уже в
 * первой ячейке строки (`aria-hidden`).
 *
 * **Ссылки** — вкладки раздела с этой гипотезой (`salesTabLink`): «Прогоны» — «Сбор базы»,
 * «Воронка» — «Воронка» (не «Отчёт»: рядом вкладка «Отчёт», а ведёт ссылка не туда).
 */

import {
  Anchor,
  Button,
  Group,
  Progress,
  Stack,
  Table,
  Text,
  useComputedColorScheme,
} from '@mantine/core';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { refusalOf } from '../api/client';
import type { HypothesisCard } from '../api/salesTypes';
import { capitalized } from '../format';
import { salesTabLink } from './leadFilters';
import { resumeRow, stopRow, usePlanAction } from './planApi';
import type { PlanPlace, PlanRow } from './planApi';
import { PlanKeys } from './PlanKeys';
import { PlanMore } from './PlanMore';
import { PlanQuality } from './PlanQuality';
import { foundShare, foundWords, needsKeys, spendWords, stageWords } from './planWords';

/** Ширина колонки: строка этапа «Сбор: топ-20, ключи 12 000 из 30 000» — в две строки (табличные
 *  цифры шире, в 256 px одной строкой не встаёт — стенд 17.11), кнопки — в два ряда. */
export const PLAN_COLUMN_WIDTH = '18rem';

type Dialog = 'quality' | 'more' | 'keys';
type Open = (dialog: Dialog, from: HTMLElement) => void;

/** Этапы, у которых есть прогоны впереди: их останавливают. */
const STOPPABLE = new Set(['waiting', 'collecting']);

/** Слова сервера рвутся где угодно — как имя в первой колонке (`cellName`). */
const BREAK_ANYWHERE = { overflowWrap: 'anywhere' } as const;

/** «гипотезы «Логистика»» — к имени кнопки или ссылки для читалки. */
function whose(row: PlanRow): string {
  return `гипотезы «${row.name}»`;
}

function DialogButton({
  dialog,
  onOpen,
  label,
  children,
}: {
  dialog: Dialog;
  onOpen: Open;
  /** Имя для читалки: видимое слово и чья строка. */
  label: string;
  children: string;
}) {
  return (
    <Button
      size="compact-xs"
      variant="default"
      className="press"
      aria-haspopup="dialog"
      aria-label={label}
      onClick={(event) => onOpen(dialog, event.currentTarget)}
    >
      {children}
    </Button>
  );
}

function Actions({ row, onOpen }: { row: PlanRow; onOpen: Open }) {
  const act = usePlanAction((stop: boolean) => (stop ? stopRow(row.id) : resumeRow(row.id)));
  const stoppable = STOPPABLE.has(row.stage);
  const resumable = row.stage === 'stopped';
  return (
    <>
      <Group gap={6} wrap="wrap">
        {(stoppable || resumable) && (
          <Button
            size="compact-xs"
            variant="default"
            className="press"
            loading={act.isPending}
            aria-label={`${stoppable ? 'Остановить' : 'Продолжить'} гипотезу «${row.name}»`}
            onClick={() => act.mutate(stoppable)}
          >
            {stoppable ? 'Остановить' : 'Продолжить'}
          </Button>
        )}
        <DialogButton dialog="quality" onOpen={onOpen} label={`Качество сайтов ${whose(row)}`}>
          Качество сайтов
        </DialogButton>
        <DialogButton dialog="more" onOpen={onOpen} label={`Собрать ещё гипотезе «${row.name}»`}>
          Собрать ещё
        </DialogButton>
        {needsKeys(row) && (
          <DialogButton
            dialog="keys"
            onOpen={onOpen}
            label={`Добавить ключи гипотезе «${row.name}»`}
          >
            Добавить ключи
          </DialogButton>
        )}
      </Group>
      {act.error !== null && (
        <Text size="xs" c="red" role="alert">
          {refusalOf(act.error)}
        </Text>
      )}
    </>
  );
}

function Links({ row }: { row: PlanRow }) {
  return (
    <Group gap="md">
      <Anchor
        component={Link}
        to={salesTabLink('collect', row.hypothesis_id)}
        size="xs"
        className="inkLink"
        aria-label={`Прогоны ${whose(row)}`}
      >
        Прогоны
      </Anchor>
      <Anchor
        component={Link}
        to={salesTabLink('funnel', row.hypothesis_id)}
        size="xs"
        className="inkLink"
        aria-label={`Воронка ${whose(row)}`}
      >
        Воронка
      </Anchor>
    </Group>
  );
}

/** «Найдено N из цели» полосой — дорожкой `Meter`, без его тяжести; заливка по теме (шапка файла). */
function FoundBar({ row }: { row: PlanRow }) {
  const dark = useComputedColorScheme('light') === 'dark';
  return (
    <Progress
      className="meter"
      value={foundShare(row)}
      color={dark ? 'var(--accent)' : 'var(--accent-deep)'}
      radius="xl"
      size="sm"
      aria-label={`«${row.name}»: ${foundWords(row)}`}
    />
  );
}

function PlanBody({ row, card }: { row: PlanRow; card: HypothesisCard }) {
  const [open, setOpen] = useState<Dialog | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const show: Open = (dialog, from) => {
    opener.current = from;
    setOpen(dialog);
  };
  const close = () => setOpen(null);
  useEffect(() => {
    if (open === null) opener.current?.focus();
  }, [open]);
  return (
    <Stack gap={4} style={BREAK_ANYWHERE}>
      <Text size="xs" c="dimmed" hiddenFrom="sm" aria-hidden>
        {row.name}
      </Text>
      <Text size="sm" fw={500}>
        {capitalized(stageWords(row))}
      </Text>
      <Text size="xs">{foundWords(row)}</Text>
      <FoundBar row={row} />
      <Text size="xs">{spendWords(row.spent_usd, row.cap_usd)}</Text>
      {row.problems.map((item) => (
        <Text key={item.code} size="xs" c="red">
          {capitalized(item.words)}
        </Text>
      ))}
      <Actions row={row} onOpen={show} />
      <Links row={row} />
      {open === 'quality' && <PlanQuality row={row} card={card} onClose={close} />}
      {open === 'more' && <PlanMore row={row} onClose={close} />}
      {open === 'keys' && <PlanKeys row={row} onClose={close} />}
    </Stack>
  );
}

/** Ячейка колонки: у гипотезы плана — её строка плана, у остальных — пусто. От левого края:
 *  кнопки, полоса и ссылки стоят слева, а абзац по центру рвётся «лесенкой» (стенд 17.11). */
export function PlanCell({ place, card }: { place: PlanPlace | undefined; card: HypothesisCard }) {
  if (place === undefined) return <Table.Td />;
  return (
    <Table.Td className="wrapCell" ta="left">
      <PlanBody row={place.row} card={card} />
    </Table.Td>
  );
}
