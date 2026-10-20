/**
 * Решение человека по сайту прогона и его отмена — на месте строки, по которой решают.
 *
 * Тест продаж 20.10: «Принять» и «Отказ» есть, а вернуть ошибочное решение нечем. Теперь:
 * - **на «На проверку»** — колонка «Решение»: «Принять» и «Отказ»; решённая здесь строка стоит на
 *   месте («принят — записываем…», потом «решено: принят») с кнопкой «Отменить решение», а после
 *   отмены — снова «Принять» и «Отказ»;
 * - **на остальных вкладках** решение человека видно в колонке «Кто решил» — там же «Отменить
 *   решение»; строка, по которой отменили здесь, решается на месте теми же кнопками, помельче.
 *
 * Кнопка отмены — только когда сервер говорит, что отмена возможна (`undoable`: решил человек, лида
 * из сайта нет). Отказ сервера (лид завели посреди, решил не человек) — словами над таблицей.
 */

import { Button, Group, Stack, Text } from '@mantine/core';

import type { SegmentSite } from './SegmentSitesApi';
import { LANE_WORDS } from './SegmentSitesWords';

/** Действие человека над строкой: решение или его отмена. */
export type SiteAction = 'accept' | 'reject' | 'undo';

/** Строка, по которой человек действовал в этом виде: стоит на месте с тем, что записал сервер. */
export interface Held {
  /** Строка, как её показывать: до ответа сервера — прежняя, после — его ответ. */
  site: SegmentSite;
  /** Что пишется сейчас; записано — `null`. */
  doing: SiteAction | null;
}

/** Пока сервер пишет — словами на месте кнопок: второе нажатие по строке не уходит. */
export function doingWords(doing: SiteAction): string {
  return doing === 'undo' ? 'отменяем решение…' : `${LANE_WORDS[doing]} — записываем…`;
}

interface SlotProps {
  /** Строка, как её показывать (`Held.site` у тех, по которым действовали здесь). */
  site: SegmentSite;
  held: Held | undefined;
  /** Строки прежнего вида ждут замены: решать по ним нельзя. */
  busy: boolean;
  /** Колонка «Решение» вкладки «На проверку» — или место под «кто решил» на остальных. */
  wide: boolean;
  onAct: (site: SegmentSite, action: SiteAction) => void;
}

function Choice({ site, busy, wide, onAct }: Omit<SlotProps, 'held'>) {
  const size = wide ? 'compact-sm' : 'compact-xs';
  return (
    <Group gap={wide ? 'xs' : 6} justify="center" wrap="nowrap">
      <Button size={size} className="press" disabled={busy} onClick={() => onAct(site, 'accept')}>
        Принять
      </Button>
      <Button
        size={size}
        variant="default"
        className="press"
        disabled={busy}
        onClick={() => onAct(site, 'reject')}
      >
        Отказ
      </Button>
    </Group>
  );
}

function Decided({ site, busy, wide, onAct }: Omit<SlotProps, 'held'>) {
  const undo = site.undoable && (
    <Button
      size="compact-xs"
      variant="subtle"
      className="press"
      disabled={busy}
      onClick={() => onAct(site, 'undo')}
    >
      Отменить решение
    </Button>
  );
  if (!wide) return undo || null;
  return (
    <Stack gap={4} align="center">
      <Text size="sm" c="dimmed" role="status">
        решено: {LANE_WORDS[site.lane]}
      </Text>
      {undo}
    </Stack>
  );
}

/**
 * Что можно сделать со строкой: пока пишется — словами; решил человек — «Отменить решение»;
 * сайт ждёт человека — «Принять» и «Отказ» (на остальных вкладках — только у строки, по которой
 * отменили здесь: решения «на проверку» — на её вкладке).
 */
export function DecisionSlot({ site, held, busy, wide, onAct }: SlotProps) {
  if (held?.doing != null) {
    return (
      <Text size="sm" c="dimmed" role="status">
        {doingWords(held.doing)}
      </Text>
    );
  }
  if (site.decided_by === 'human') {
    return <Decided site={site} busy={busy} wide={wide} onAct={onAct} />;
  }
  if (site.lane === 'review' && (wide || held !== undefined)) {
    return <Choice site={site} busy={busy} wide={wide} onAct={onAct} />;
  }
  return null;
}
