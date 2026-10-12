/**
 * Поле числа версии на «Порогах»: число в поле — как набрано, границы — у значка «i»,
 * отказ — под полем, до нажатия. Одно на пороги отбора и правило региона со сроком
 * метрик (`RunRulesFields`): подпись, подсказка с границами, ширина и отказ у них
 * общие, и поле, поправленное в одном месте, не расходится со вторым.
 *
 * **Поле — по числу**: самому длинному, «10 000 000» с полями, хватает 115 px (замер
 * 28.09.2026, тогда ещё со стрелками). До 09.10.2026 поле стояло во всю колонку подписи —
 * 200 px под «20», то самое «поле больше своего значения» (замечание Anthony
 * 09.10.2026). Колонка — по своей подписи: подпись не переносится (`nowrap`), и
 * соседние поля стоят на одной высоте.
 *
 * Подпись — не `<label>`: в ней кнопка подсказки, а кнопка внутри подписи поля —
 * вложенный элемент формы, и её имя вошло бы в имя поля. Имя полю — `aria-label`,
 * словами подписи.
 */

import { Group } from '@mantine/core';

import type { ThresholdRange } from '../api/types';
import { InfoHint } from '../components/InfoHint';
import { NumberField } from '../components/NumberField';
import { rangeText } from '../components/numberText';
import { fieldRefusal } from './thresholdDraft';

const INPUT_WIDTH = '7.5rem';

interface Props {
  label: string;
  /** Что число значит — в подсказке «i», перед границами. */
  hint: string;
  range: ThresholdRange;
  value: string;
  canEdit: boolean;
  onChange: (text: string) => void;
}

export function LimitField({ label, hint, range, value, canEdit, onChange }: Props) {
  return (
    <NumberField
      labelProps={{ labelElement: 'div' }}
      label={
        <Group component="span" gap={4} wrap="nowrap">
          {label}
          <InfoHint name={`Допустимые значения: ${label}`} width={260}>
            {hint} Допустимо: целое число {rangeText(range)}
          </InfoHint>
        </Group>
      }
      aria-label={label}
      styles={{ wrapper: { width: INPUT_WIDTH } }}
      // Ни число за границей, ни дробь, ни буква не подменяются молча:
      // поле остаётся как набрано, под ним — почему так нельзя.
      disabled={!canEdit}
      value={value}
      error={canEdit ? fieldRefusal(value, range) : null}
      onChange={onChange}
    />
  );
}
