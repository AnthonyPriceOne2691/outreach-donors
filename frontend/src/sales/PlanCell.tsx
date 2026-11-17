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
 * **Ссылки** — вкладки раздела с этой гипотезой (`salesTabLink`): «Прогоны» — «Сбор базы»,
 * «Воронка» — «Воронка» (не «Отчёт»: рядом вкладка «Отчёт», а ведёт ссылка не туда).
 */

import { Anchor, Button, Group, Progress, Stack, Table, Text } from '@mantine/core';
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
export const PLAN_COLUMN_PX = 288;

type Dialog = 'quality' | 'more' | 'keys';
type Open = (dialog: Dialog, from: HTMLElement) => void;

/** Этапы, у которых есть прогоны впереди: их останавливают. */
const STOPPABLE = new Set(['waiting', 'collecting']);

/** Слова сервера рвутся где угодно — как имя в первой колонке (`cellName`). */
const BREAK_ANYWHERE = { overflowWrap: 'anywhere' } as const;

function DialogButton({
  dialog,
  onOpen,
  children,
}: {
  dialog: Dialog;
  onOpen: Open;
  children: string;
}) {
  return (
    <Button
      size="compact-xs"
      variant="default"
      className="press"
      aria-haspopup="dialog"
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
        <DialogButton dialog="quality" onOpen={onOpen}>
          Качество сайтов
        </DialogButton>
        <DialogButton dialog="more" onOpen={onOpen}>
          Собрать ещё
        </DialogButton>
        {needsKeys(row) && (
          <DialogButton dialog="keys" onOpen={onOpen}>
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

function Links({ hypothesis }: { hypothesis: number }) {
  return (
    <Group gap="md">
      <Anchor
        component={Link}
        to={salesTabLink('collect', hypothesis)}
        size="xs"
        className="inkLink"
      >
        Прогоны
      </Anchor>
      <Anchor
        component={Link}
        to={salesTabLink('funnel', hypothesis)}
        size="xs"
        className="inkLink"
      >
        Воронка
      </Anchor>
    </Group>
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
      <Text size="sm" fw={500}>
        {capitalized(stageWords(row))}
      </Text>
      <Text size="xs">{foundWords(row)}</Text>
      <Progress
        value={foundShare(row)}
        size="sm"
        aria-label={`«${row.name}»: ${foundWords(row)}`}
      />
      <Text size="xs">{spendWords(row.spent_usd, row.cap_usd)}</Text>
      {row.problems.map((item) => (
        <Text key={item.code} size="xs" c="red">
          {capitalized(item.words)}
        </Text>
      ))}
      <Actions row={row} onOpen={show} />
      <Links hypothesis={row.hypothesis_id} />
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
