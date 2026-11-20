/**
 * «Потолок плана, $» — окно у строки плана над таблицей гипотез: потолок плана меняется и после
 * сохранения: потолок — по смете с запасом, его можно поменять.
 *
 * Поднятый потолок снимает паузу плана, вставшего на своём потолке, — план снова идёт; на паузе по
 * другой причине («Остановить план», счёт выдачи, модель) план так и стоит: его снимает «Продолжить».
 * Под полем — израсходованное словами отказа сервера и сумма, от которой потолок можно задать
 * (`capFloorWords`); ниже израсходованного сервер отказывает словами — плашкой над полем, набранное
 * на месте. Тот же потолок — окно просто закрывается. Потолок гипотезы — в окне «Качество сайтов».
 *
 * Каркас — окна кнопок гипотезы плана (`PlanDialog`): заголовок короткий, имя плана — строкой под
 * ним. Поле — `PlanNumberField`: точка и запятая не стираются на ходу.
 */

import { useState } from 'react';

import { notify } from '../notices';
import { changePlanCap, usePlanAction } from './planApi';
import type { PlanLive } from './planApi';
import { PlanDialog } from './PlanDialog';
import { capError, capFloorWords, PlanNumberField } from './PlanNumberField';
import { dollars } from './PlanPreviewView';

interface Props {
  plan: PlanLive;
  onClose: () => void;
}

export function PlanCap({ plan, onClose }: Props) {
  const [cap, setCap] = useState<number | null>(plan.cap_usd);
  const send = usePlanAction(
    (capUsd: number) => changePlanCap(plan.id, capUsd),
    (found) => {
      notify({
        message: `«${plan.name}»: потолок плана — ${dollars(found.cap_usd)}`,
        color: 'green',
      });
      onClose();
    },
  );
  const spent = plan.hypotheses.reduce((sum, row) => sum + row.spent_usd, 0);
  const submit = () => {
    if (cap === null || cap === plan.cap_usd) onClose();
    else send.mutate(cap);
  };
  return (
    <PlanDialog
      title="Потолок плана"
      name={plan.name}
      refused="Потолок не сохранён"
      error={send.error}
      busy={send.isPending}
      ready={cap !== null && cap > 0}
      action="Сохранить"
      onSubmit={submit}
      onClose={onClose}
    >
      <PlanNumberField
        label="Потолок плана, $"
        description={`${capFloorWords(spent)}. Поднятый потолок снимает паузу на потолке`}
        min={0}
        decimalScale={2}
        allowNegative={false}
        value={cap}
        onValue={setCap}
        error={capError(cap)}
        data-autofocus
      />
    </PlanDialog>
  );
}
