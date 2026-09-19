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

import type { Permission, UserCard } from '../api/types';

const ACTIONS: { key: Permission; title: string }[] = [
  { key: 'view', title: 'Смотреть базу' },
  { key: 'run', title: 'Запускать прогоны' },
  { key: 'settings', title: 'Править пороги' },
  { key: 'send', title: 'Отправлять письма' },
  { key: 'users', title: 'Заводить учётки' },
];

interface Props {
  user: UserCard;
  disabled: boolean;
  onChange: (permissions: Partial<Record<Permission, boolean>>) => void;
}

export function PermissionsPopover({ user, disabled, onChange }: Props) {
  const [opened, setOpened] = useState(false);
  const overrides = user.overrides;

  const set = (key: Permission, allowed: boolean) => {
    onChange({ ...overrides, [key]: allowed });
  };

  const back = (key: Permission) => {
    const next = { ...overrides };
    delete next[key];
    onChange(next);
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
      <Popover.Dropdown className="glassPanel">
        <Stack gap="xs" w={320}>
          {ACTIONS.map(({ key, title }) => {
            const override = overrides[key];
            return (
              <Group key={key} justify="space-between" wrap="nowrap">
                <div>
                  <Text size="sm">{title}</Text>
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
                    aria-label={title}
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
