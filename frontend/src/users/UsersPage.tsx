/**
 * Учётки: завести, выдать права, сбросить пароль, отключить.
 *
 * Удаления нет и не будет: вместе с учёткой ушла бы история её действий —
 * кто менял пороги, кто отправил письмо. Отключение оставляет историю
 * и действует сразу, потому что активность проверяется на каждом запросе.
 *
 * Отказы сервера показываются целиком. Три из них человек увидит чаще
 * прочих — «почта занята», «нельзя снять права с себя», «последний
 * действующий админ», — и все три объясняют, что делать.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  ScrollArea,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { IconUserPlus } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { AccessPatch, OneTimePassword, Role, UserCard } from '../api/types';
import { listUsers, patchUser, resetPassword } from '../api/users';
import { useSession } from '../auth/AuthProvider';
import { formatDateTime } from '../format';
import { CreateUserModal } from './CreateUserModal';
import { OneTimePasswordModal } from './OneTimePasswordModal';
import { PermissionsPopover } from './PermissionsPopover';

const USERS_QUERY_KEY = ['users'] as const;

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

export function UsersPage() {
  const queryClient = useQueryClient();
  const { user: me, refresh } = useSession();
  const [creating, setCreating] = useState(false);
  const [issued, setIssued] = useState<OneTimePassword | null>(null);

  const {
    data: users,
    isLoading,
    error,
  } = useQuery({
    queryKey: USERS_QUERY_KEY,
    queryFn: listUsers,
  });

  const change = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: AccessPatch }) => patchUser(id, patch),
    onSuccess: async (updated) => {
      await queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY });
      // Правка своих прав меняет то, что человек видит прямо сейчас,
      // — карточку себя надо перечитать, иначе меню останется прежним.
      if (updated.id === me?.id) await refresh();
      notifications.show({ message: `Учётка ${updated.email} обновлена`, color: 'green' });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не изменили', message: refusalOf(failure), color: 'red' }),
  });

  const reset = useMutation({
    mutationFn: (id: number) => resetPassword(id),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY });
      setIssued(result);
    },
    onError: (failure) =>
      notifications.show({ title: 'Не сбросили', message: refusalOf(failure), color: 'red' }),
  });

  if (isLoading) return <Loader aria-label="Загружаем учётки" m="md" />;

  if (error) {
    return (
      <Alert color="red" title="Список не загрузился" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  const rows = (users ?? []).map((user: UserCard) => (
    <Table.Tr key={user.id} opacity={user.is_active ? 1 : 0.55}>
      <Table.Td>
        <Text fw={user.id === me?.id ? 600 : 400}>{user.email}</Text>
        {user.must_change_password && (
          <Badge size="xs" color="yellow">
            не сменил разовый пароль
          </Badge>
        )}
      </Table.Td>
      <Table.Td>
        <Group justify="center">
          <Select
            size="xs"
            w={140}
            allowDeselect={false}
            aria-label={`Роль ${user.email}`}
            value={user.role}
            data={[
              { value: 'operator', label: 'оператор' },
              { value: 'admin', label: 'админ' },
            ]}
            onChange={(value) =>
              value !== null && change.mutate({ id: user.id, patch: { role: value as Role } })
            }
          />
        </Group>
      </Table.Td>
      <Table.Td>
        <Group justify="center">
          <PermissionsPopover
            user={user}
            disabled={change.isPending}
            onChange={(permissions) => change.mutate({ id: user.id, patch: { permissions } })}
          />
        </Group>
      </Table.Td>
      <Table.Td>
        <Group justify="center">
          <Switch
            aria-label={`Учётка ${user.email} включена`}
            checked={user.is_active}
            onChange={(event) =>
              change.mutate({ id: user.id, patch: { is_active: event.currentTarget.checked } })
            }
          />
        </Group>
      </Table.Td>
      <Table.Td>
        <Text size="sm" c="dimmed">
          {user.last_login_at === null ? 'ни разу' : formatDateTime(user.last_login_at)}
        </Text>
      </Table.Td>
      <Table.Td>
        <Button
          size="compact-sm"
          variant="default"
          className="press"
          loading={reset.isPending && reset.variables === user.id}
          onClick={() => reset.mutate(user.id)}
        >
          Сбросить пароль
        </Button>
      </Table.Td>
    </Table.Tr>
  ));

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Group justify="space-between" align="flex-start">
          <Stack gap={6}>
            <Title order={3}>Учётки</Title>
            <Text size="sm" c="dimmed" maw={560}>
              Учётки не удаляются: вместе с ней ушла бы история действий. Отключённая учётка
              перестаёт пускать сразу, даже с непросроченным пропуском.
            </Text>
          </Stack>
          <Button
            className="press"
            variant="gradient"
            leftSection={<IconUserPlus size={18} />}
            onClick={() => setCreating(true)}
          >
            Завести учётку
          </Button>
        </Group>
      </Card>

      <Card className="glass" p="xs">
        <ScrollArea className="scrollSlim" type="auto">
          <Table className="dataTable" verticalSpacing="sm" horizontalSpacing="md" miw={760}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Почта</Table.Th>
                <Table.Th>Роль</Table.Th>
                <Table.Th>Права</Table.Th>
                <Table.Th>Включена</Table.Th>
                <Table.Th>Последний вход</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>{rows}</Table.Tbody>
          </Table>
        </ScrollArea>
      </Card>

      <CreateUserModal
        opened={creating}
        onClose={() => setCreating(false)}
        onCreated={(result) => {
          setCreating(false);
          setIssued(result);
          void queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY });
        }}
      />
      <OneTimePasswordModal issued={issued} onClose={() => setIssued(null)} />
    </Stack>
  );
}
