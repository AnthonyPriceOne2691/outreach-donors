/**
 * Переключатель этапа: донорам или рекламодателям.
 *
 * Один на все экраны, где этап выбирают (письма, агент переписки): подписи
 * этапов и вид переключателя не расходятся между экранами.
 *
 * **На телефоне — столбиком во всю ширину**, как период воронки продаж
 * (`sales/FunnelPane`). В ряд «Донорам · Рекламодателям · Бизнесам ниши» —
 * около 360 px, а панели на телефоне достаётся 286: на 390 px третий этап
 * уходил за край экрана и срезался (проверка QA 10.10.2026, «Письма»). Столбик,
 * а не список: этапов два-три, и все видны и нажимаются сразу.
 *
 * **Что экран делает на выбранном этапе — в «i» рядом с переключателем** (слово владельца
 * 10.11.2026): абзац под ним в три строки отодвигал то, по чему решают. Пояснения длиннее
 * подсказки в строку, поэтому «i» — окошком по нажатию. На виду остаются состояние и поля
 * экрана — их ставит сам экран, а не этот компонент.
 *
 * **Выбрать из одного нечего** — переключателя нет, и пояснение — в «i» у заголовка экрана:
 * его ставит экран (`PageHead`, `hint`), здесь не рисуется ничего. До 10.11.2026 оно стояло
 * под заголовком строкой («Письма донорам», 23.10.2026).
 */

import { Group, SegmentedControl } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import type { ReactNode } from 'react';

import type { LetterStage } from '../api/types';
import { InfoHint } from './InfoHint';

/** Этап на выбор: значение и подпись. */
export interface StageOption<S extends string> {
  value: S;
  label: string;
}

const STAGES: StageOption<LetterStage>[] = [
  { value: 'donors', label: 'Донорам' },
  { value: 'advertisers', label: 'Рекламодателям' },
];

/** Узкое окно — то же, что у вкладок «Диалогов» и периода воронки. */
const NARROW = '(max-width: 36em)';

interface StageSwitchProps<S extends string> {
  /** Имя переключателя для программ чтения с экрана и тестов. */
  label: string;
  value: S;
  onChange: (stage: S) => void;
  /** Пояснение к выбранному этапу — в «i» рядом с переключателем. */
  lead: ReactNode;
  /** Этапы на выбор; нет — доноры и рекламодатели. Экран агента берёт их из
   *  реестра сервера: новый этап встаёт на него без правки экрана. */
  stages?: StageOption<S>[];
}

export function StageSwitch<S extends string = LetterStage>({
  label,
  value,
  onChange,
  lead,
  stages,
}: StageSwitchProps<S>) {
  // Значение — с первой отрисовки: иначе телефон на миг рисовал бы ряд за краем
  // экрана и перестраивал его на глазах (как `layout/split`).
  const narrow = useMediaQuery(NARROW, false, { getInitialValueInEffect: false }) === true;
  const options = stages ?? STAGES;
  if (options.length < 2) return null;
  const chosen = options.find((one) => one.value === value)?.label ?? label;
  return (
    <Group gap={4} wrap="nowrap" align="center">
      <SegmentedControl
        aria-label={label}
        value={value}
        onChange={(picked) => onChange(picked as S)}
        data={options}
        orientation={narrow ? 'vertical' : 'horizontal'}
        fullWidth={narrow}
        style={{ flex: narrow ? '1 1 auto' : '0 0 auto', minWidth: 0 }}
      />
      <InfoHint name={`Пояснение к «${chosen}»`} width={360} interactive>
        {lead}
      </InfoHint>
    </Group>
  );
}
