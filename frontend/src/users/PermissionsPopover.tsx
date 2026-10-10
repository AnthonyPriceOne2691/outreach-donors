/**
 * Точечные права поверх роли.
 *
 * Показываются обе стороны: что человек может сейчас и чем это отличается
 * от его роли. Разница важна — отобрать обратно можно только выданное
 * поимённо, а «как у роли» и «выдано отдельно» выглядят в интерфейсе
 * одинаково, если не сказать прямо.
 *
 * Переключатель ставит исключение, «как у роли» — снимает его. Без
 * третьего состояния было бы не отличить «оператору отправка не положена»
 * от «отправку у него отобрали».
 */

import { Badge, Button, Group, Popover, Stack, Switch, Text } from '@mantine/core';
import { useState } from 'react';

import { PERMISSION_TITLES } from '../api/labels';
import type { Permission, UserCard } from '../api/types';

/** Все права — из одного списка с их словами (`PERMISSION_TITLES`). До 09.10.2026 здесь
 *  стояли пять из восьми: «подтверждать цены», «домены рассылки» и «раздел продаж»
 *  нельзя было ни выдать, ни снять, хотя сервер принимает исключение по любому праву,
 *  а экран отказа отправляет именно к админу (аудит экранов 09.10.2026). */
const ACTIONS = Object.keys(PERMISSION_TITLES) as Permission[];

function title(permission: Permission): string {
  const words = PERMISSION_TITLES[permission];
  return words.charAt(0).toUpperCase() + words.slice(1);
}

type Overrides = UserCard['overrides'];

interface Props {
  user: UserCard;
  disabled: boolean;
  /** Правка — функцией от последних известных исключений учётки, а не готовым набором.
   *  Набор, собранный при щелчке, брался из списка, загруженного до прошлой правки: второй
   *  быстрый переключатель затирал первый, хотя оба «обновлены» (QA 10.10.2026). Из чего
   *  собирать, решает страница — когда до правки дойдёт очередь (`UsersPage`). */
  onChange: (next: (overrides: Overrides) => Overrides) => void;
}

export function PermissionsPopover({ user, disabled, onChange }: Props) {
  const [opened, setOpened] = useState(false);
  const overrides = user.overrides;

  const set = (key: Permission, allowed: boolean) => {
    onChange((latest) => ({ ...latest, [key]: allowed }));
  };

  const back = (key: Permission) => {
    onChange((latest) => {
      const next = { ...latest };
      delete next[key];
      return next;
    });
  };

  return (
    <Popover
      opened={opened}
      onChange={setOpened}
      position="bottom-end"
      withArrow
      radius="lg"
      shadow="md"
      transitionProps={{ transition: 'pop', duration: 180 }}
    >
      <Popover.Target>
        <Button
          size="compact-sm"
          variant="subtle"
          disabled={disabled}
          onClick={() => setOpened((o) => !o)}
        >
          Права ({user.permissions.length})
        </Button>
      </Popover.Target>
      <Popover.Dropdown className="glassSolid">
        <Stack gap="xs" w={320}>
          {ACTIONS.map((key) => {
            const override = overrides[key];
            return (
              <Group key={key} justify="space-between" wrap="nowrap">
                <div>
                  <Text size="sm">{title(key)}</Text>
                  {override === undefined ? (
                    <Text size="xs" c="dimmed">
                      как у роли
                    </Text>
                  ) : (
                    <Badge size="xs" color={override ? 'green' : 'red'}>
                      {override ? 'выдано отдельно' : 'отобрано'}
                    </Badge>
                  )}
                </div>
                <Group gap="xs" wrap="nowrap">
                  {override !== undefined && (
                    <Button size="compact-xs" variant="subtle" onClick={() => back(key)}>
                      как у роли
                    </Button>
                  )}
                  <Switch
                    aria-label={title(key)}
                    checked={user.permissions.includes(key)}
                    onChange={(event) => set(key, event.currentTarget.checked)}
                  />
                </Group>
              </Group>
            );
          })}
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
