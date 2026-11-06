/**
 * Ключ Ahrefs и наш кап на экране «Расход»: какого размера ключ и когда спросили, цикл
 * и обнуление, сколько потратили мы и сколько выбрано на ключе всего, сколько осталось, что
 * задано у нас — и «Изменить кап» у того, кому это право выдано.
 *
 * **Кап — от ключа** (решение владельца 16.10.2026: «сервис должен владеть текущей ситуацией
 * по ключу»). Размер ключа и дату обнуления называет сам Ahrefs; на экране кап можно только
 * сузить («не больше N»), а по умолчанию он — весь лимит ключа и следует за ключом. До этого
 * кап жил в окружении сервера и считался с первого числа, а ключ обнуляется в свой день: кап
 * показывал почти выбранным, когда у ключа оставалась большая часть.
 *
 * **Два периода — подписаны оба**: карточка капа — цикл ключа («с 21 октября»), статьи и плитки
 * на своей вкладке — календарный месяц. Иначе два числа Ahrefs читались бы одним.
 *
 * **Экран называет, а не считает**: «осталось» — до того, во что упрёмся раньше (наш кап, лимит
 * ключа или пространства), и доля — числами сервера, тем же правилом, что у тревог сторожа.
 *
 * **Право — «править пороги»**, то же, что у порогов. Без него — только чтение и строка,
 * у кого это право: отсутствие права объясняется на месте (правило экранов).
 */

import { useState } from 'react';

import { permissionTitle } from '../api/labels';
import type { AhrefsKey, CapBinding } from '../api/settings';
import { useSession } from '../auth/AuthProvider';
import { formatDay, formatNumber } from '../format';
import { AhrefsCapModal } from './AhrefsCapModal';
import { changedBy } from './CapDialog';
import { checked, LimitCard, LimitLine } from './LimitCard';

/** Во что упрёмся раньше — словами строки «осталось». */
const BINDING: Record<CapBinding, string> = {
  cap: 'упирается в наш кап',
  key: 'упирается в лимит ключа',
  workspace: 'упирается в лимит пространства Ahrefs',
};

/** Размер ключа — строкой, только когда он не очевиден из «из N юнитов»: неизвестен или это лимит
 *  пространства; `null` — ключ со своим лимитом. */
function keyLine(key: AhrefsKey): string | null {
  if (key.size === null) return key.unknown ?? 'Размер ключа Ahrefs неизвестен';
  if (key.own_limit) return null;
  return `Своего лимита у ключа нет — действует лимит пространства, ${formatNumber(key.size)} юнитов за цикл`;
}

/** Цикл капа: от обнуления ключа, а без его даты — календарный месяц, и сказано почему. */
function cycleLine(key: AhrefsKey): string {
  return key.by_key
    ? `Цикл ключа: с ${formatDay(key.cycle_start)}`
    : `Цикл — календарный месяц, с ${formatDay(key.cycle_start)}: Ahrefs не назвал даты обнуления ключа`;
}

/** Потрачено нами и выбрано всего — на ключе или, без своего лимита, в пространстве. */
function spentLine(key: AhrefsKey): string {
  const ours = `Потрачено нами за цикл: ${formatNumber(key.spent_by_us)}`;
  if (key.size === null) return ours;
  if (key.own_limit && key.key_used !== null) {
    return `${ours} · выбрано на ключе всего ${formatNumber(key.key_used)} — вместе с соседней системой на общем ключе`;
  }
  if (key.workspace_used === null) return ours;
  return `${ours} · выбрано в пространстве Ahrefs всего ${formatNumber(key.workspace_used)} — всеми его ключами`;
}

/** Что задано у нас: весь лимит ключа, «не больше N» — или N выше ключа, и действует ключ. */
function chosenLine(key: AhrefsKey): string {
  if (key.chosen === null) return 'Наш кап: весь лимит ключа';
  if (key.above_key && key.size !== null) {
    return `Наш кап: не больше ${formatNumber(key.chosen)} — выше лимита ключа ${formatNumber(key.size)}, действует ${formatNumber(key.size)}`;
  }
  return `Наш кап: не больше ${formatNumber(key.chosen)} юнитов за цикл`;
}

/** Кто и когда поставил то, что задано; смен нет — весь лимит ключа по умолчанию. */
function changedLine(key: AhrefsKey): string {
  const [last] = key.changes;
  return last === undefined
    ? 'Кап на экране ещё не меняли — весь лимит ключа'
    : `Кап изменён: ${changedBy(last)}`;
}

/** Можно ли менять кап и кто меняет, если нельзя. Продажи ключом не пользуются (слово Anthony 06.11.2026). */
function useCapRight(): { allowed: boolean; who: string } {
  const { can } = useSession();
  const inOutreach = can('donors') || can('advertisers');
  return {
    allowed: can('settings') && inOutreach,
    who: inOutreach
      ? `Кап меняет учётка с правом «${permissionTitle('settings')}»`
      : 'Кап меняет учётка доноров или рекламодателей: продажи им не пользуются',
  };
}

export function AhrefsCap({ ahrefsKey: key }: { ahrefsKey: AhrefsKey }) {
  const right = useCapRight();
  const [editing, setEditing] = useState(false);
  const known = key.cap !== null;
  const size = keyLine(key);
  const asked = key.asked_at === null ? '' : ` · ${checked(key.asked_at)}`;
  return (
    <>
      <LimitCard
        title="Ahrefs"
        caption="Осталось"
        value={known ? formatNumber(key.left) : undefined}
        of={known ? `из ${formatNumber(key.cap)} юнитов` : undefined}
        gauge={
          key.cap === null
            ? undefined
            : {
                spent: key.cap - key.left,
                cap: key.cap,
                label: 'Выбрано от капа Ahrefs за цикл',
              }
        }
        note={
          known
            ? `Выбрано ${key.used_percent ?? 0}%${key.binding === null ? '' : `, ${BINDING[key.binding]}`}`
            : 'Кап неизвестен, и прогоны не запустятся, пока Ahrefs не ответит: тратить не от чего.'
        }
        when={`Сброс ${formatDay(key.next_reset)}${asked}`}
        footer={chosenLine(key)}
        action={{
          label: 'Изменить кап',
          ...right,
          onClick: () => setEditing(true),
        }}
      >
        {size !== null && <LimitLine strong>{size}</LimitLine>}
        <LimitLine>{cycleLine(key)}</LimitLine>
        <LimitLine>{spentLine(key)}</LimitLine>
        <LimitLine>{changedLine(key)}</LimitLine>
      </LimitCard>
      {/* Окно монтируется на каждое открытие — и открывается на действующем выборе. */}
      {editing && <AhrefsCapModal ahrefsKey={key} onClose={() => setEditing(false)} />}
    </>
  );
}
