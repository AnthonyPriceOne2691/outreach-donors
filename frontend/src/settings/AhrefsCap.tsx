/**
 * Месячный кап юнитов Ahrefs на экране «Расход»: сколько потрачено из него, сколько осталось,
 * кто и когда его менял — и «Изменить кап» у того, кому это право выдано.
 *
 * Решение владельца 16.10.2026 (требование: «Расход — сколько потрачено, на что, сколько
 * осталось до капа»; «видно, кто и когда менял»). До этого кап жил только в окружении
 * сервера, и смета прогона советовала «поднимите кап», не говоря где.
 *
 * **Три числа, и путать их нельзя.** Остаток у провайдера включает траты соседней системы
 * на общем ключе; с капом сравнивают наш расход. Остаток по капу экран называет, а не
 * считает (`ahrefs_cap_left`): сервер считает его тем же правилом, что потолок прогона.
 *
 * **Право — «править пороги»**, то же, что у порогов. Без него — только чтение и строка,
 * у кого это право: отсутствие права объясняется на месте (правило экранов).
 */

import { Button, Group, Stack, Text } from '@mantine/core';
import { useState } from 'react';

import { permissionTitle } from '../api/labels';
import type { UsageView } from '../api/settings';
import { useSession } from '../auth/AuthProvider';
import { Meter } from '../components/Meter';
import { formatNumber } from '../format';
import { AhrefsCapModal, changedBy } from './AhrefsCapModal';

/** Кто и когда поставил действующий кап; смен нет — кап по умолчанию сервера. */
function changedLine(data: UsageView): string {
  const [last] = data.ahrefs_cap_changes;
  return last === undefined
    ? 'Кап по умолчанию сервера — на экране его ещё не меняли'
    : `Кап изменён: ${changedBy(last)}`;
}

export function AhrefsCap({ data }: { data: UsageView }) {
  const { can } = useSession();
  const [editing, setEditing] = useState(false);
  return (
    <Stack gap={6}>
      {/* Остаток у провайдера — отдельно от капа: он включает траты соседней системы
          на общем ключе, а с капом сравнивают наш расход. Первая версия экрана путала их
          и показывала «израсходовано 0» при шести тысячах потраченных юнитов. */}
      <Group justify="space-between">
        <Text size="sm">
          Юниты Ahrefs: потрачено за месяц <b>{formatNumber(data.ahrefs_spent_by_us)}</b> из капа{' '}
          <b>{formatNumber(data.ahrefs_cap)}</b>, осталось{' '}
          <b>{formatNumber(data.ahrefs_cap_left)}</b>
        </Text>
        <Text size="sm" c="dimmed">
          {data.ahrefs_left === null
            ? 'остаток у провайдера неизвестен'
            : `у провайдера осталось ${formatNumber(data.ahrefs_left)} — с учётом чужих трат на общем ключе`}
        </Text>
      </Group>
      <Meter
        spent={data.ahrefs_spent_by_us}
        cap={data.ahrefs_cap}
        label="Юниты Ahrefs с начала месяца"
      />
      <Group gap="sm" wrap="wrap">
        <Text size="sm" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
          {changedLine(data)}
        </Text>
        {can('settings') ? (
          <Button
            size="compact-sm"
            variant="default"
            className="press"
            onClick={() => setEditing(true)}
          >
            Изменить кап
          </Button>
        ) : (
          <Text size="sm" c="dimmed">
            Кап меняет учётка с правом «{permissionTitle('settings')}»
          </Text>
        )}
      </Group>
      {/* Окно монтируется на каждое открытие — и открывается на действующем капе. */}
      {editing && (
        <AhrefsCapModal
          cap={data.ahrefs_cap}
          limits={data.ahrefs_cap_limits}
          changes={data.ahrefs_cap_changes}
          onClose={() => setEditing(false)}
        />
      )}
    </Stack>
  );
}
