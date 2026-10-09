/**
 * «Вошли как» — в шапке, у почты (аудит экранов 09.10.2026).
 *
 * До 09.10 кто вошёл, его права и «Сменить пароль» стояли карточкой наверху
 * «Обзора»: видно только с главной, а над «Ждут человека» стояло то, что смотрят
 * раз в неделю. Теперь почта — кнопка в шапке любого экрана, роль, права и смена
 * пароля — в её меню. «Выйти» — рядом отдельной кнопкой: выход не прячется.
 *
 * На телефоне почта не помещается — остаётся значок с тем же названием.
 */

import { Badge, Button, Group, Menu, Text } from '@mantine/core';
import { IconChevronDown, IconKey, IconUserCircle } from '@tabler/icons-react';
import { Link } from 'react-router-dom';

import { permissionTitle, ROLE_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';

export function AccountMenu() {
  const { user } = useSession();
  if (user === null) return null;
  return (
    <Menu position="bottom-end" withArrow shadow="md" radius="lg" width={320}>
      <Menu.Target>
        <Button
          variant="subtle"
          size="compact-sm"
          className="press"
          aria-label={`Вошли как ${user.email}`}
          leftSection={<IconUserCircle size={16} />}
          rightSection={<IconChevronDown size={14} />}
        >
          <Text span size="sm" visibleFrom="md">
            Вошли как <b>{user.email}</b>
          </Text>
        </Button>
      </Menu.Target>
      <Menu.Dropdown className="glassSolid">
        <Menu.Label>Роль</Menu.Label>
        <Group px="sm" pb="xs">
          <Badge variant="light">{ROLE_TITLES[user.role]}</Badge>
        </Group>
        <Menu.Label>Права · {user.permissions.length}</Menu.Label>
        <Group gap="xs" px="sm" pb="xs">
          {user.permissions.map((permission) => (
            <Badge key={permission} variant="light">
              {permissionTitle(permission)}
            </Badge>
          ))}
        </Group>
        <Menu.Divider />
        <Menu.Item component={Link} to="/password" leftSection={<IconKey size={16} />}>
          Сменить пароль
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}
