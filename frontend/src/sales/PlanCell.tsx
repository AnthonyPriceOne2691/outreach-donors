/**
 * Колонка «План работ» таблицы гипотез — только у гипотез плана: этап словами, «найдено N из цели»
 * с полосой, расход из потолка гипотезы, проблемы строками, кнопки и ссылки.
 *
 * **Кнопки — по этапу.** Собирает или ждёт очереди — «Остановить»; остановлена — «Продолжить»
 * (сервер продолжит тот же прогон: выдача второй раз не покупается). Набравшую цель и готовую не
 * останавливают — прогонов у неё уже нет. «Качество сайтов» и «Собрать ещё» — всегда, «Добавить
 * ключи» — при «нужны ключи». Отказ сервера — словами под кнопками.
 *
 * **Ссылки** — вкладки раздела с этой гипотезой (`salesTabLink`): «Прогоны» — «Сбор базы», «Отчёт»
 * — «Воронка».
 */

import { Anchor, Button, Group, Progress, Stack, Table, Text } from '@mantine/core';
import { useState } from 'react';
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

/** Ширина колонки: «сбор: топ-20, ключи 12 000 из 30 000» и кнопки в два ряда. */
export const PLAN_COLUMN_WIDTH = '18rem';
export const PLAN_COLUMN_PX = 288;

type Dialog = 'quality' | 'more' | 'keys';

/** Этапы, у которых есть прогоны впереди: их останавливают. */
const STOPPABLE = new Set(['waiting', 'collecting']);

function StopResume({ row }: { row: PlanRow }) {
  const act = usePlanAction((stop: boolean) => (stop ? stopRow(row.id) : resumeRow(row.id)));
  const stoppable = STOPPABLE.has(row.stage);
  if (!stoppable && row.stage !== 'stopped') return null;
  return (
    <>
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
      {act.error !== null && (
        <Text size="xs" c="red" role="alert" w="100%">
          {refusalOf(act.error)}
        </Text>
      )}
    </>
  );
}

function Actions({ row, onOpen }: { row: PlanRow; onOpen: (dialog: Dialog) => void }) {
  return (
    <Group gap={6} wrap="wrap">
      <StopResume row={row} />
      <Button
        size="compact-xs"
        variant="default"
        className="press"
        aria-haspopup="dialog"
        onClick={() => onOpen('quality')}
      >
        Качество сайтов
      </Button>
      <Button
        size="compact-xs"
        variant="default"
        className="press"
        aria-haspopup="dialog"
        onClick={() => onOpen('more')}
      >
        Собрать ещё
      </Button>
      {needsKeys(row) && (
        <Button
          size="compact-xs"
          variant="default"
          className="press"
          aria-haspopup="dialog"
          onClick={() => onOpen('keys')}
        >
          Добавить ключи
        </Button>
      )}
    </Group>
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
        Отчёт
      </Anchor>
    </Group>
  );
}

function PlanBody({ row, card }: { row: PlanRow; card: HypothesisCard }) {
  const [open, setOpen] = useState<Dialog | null>(null);
  const close = () => setOpen(null);
  return (
    <Stack gap={4}>
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
      <Actions row={row} onOpen={setOpen} />
      <Links hypothesis={row.hypothesis_id} />
      {open === 'quality' && <PlanQuality row={row} card={card} onClose={close} />}
      {open === 'more' && <PlanMore row={row} onClose={close} />}
      {open === 'keys' && <PlanKeys row={row} onClose={close} />}
    </Stack>
  );
}

/** Ячейка колонки: у гипотезы плана — её строка плана, у остальных — пусто. */
export function PlanCell({ place, card }: { place: PlanPlace | undefined; card: HypothesisCard }) {
  if (place === undefined) return <Table.Td />;
  return (
    <Table.Td className="wrapCell">
      <PlanBody row={place.row} card={card} />
    </Table.Td>
  );
}
