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
} from '@mantine/core';
import { IconUserPlus } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import type { AccessPatch, OneTimePassword, Role, UserCard } from '../api/types';
import { listUsers, patchUser, resetPassword } from '../api/users';
import { useSession } from '../auth/AuthProvider';
import { ConfirmPopover } from '../components/ConfirmPopover';
import { PageHead } from '../components/PageHead';
import { formatDateTime } from '../format';
import { CreateUserModal } from './CreateUserModal';
import { OneTimePasswordModal } from './OneTimePasswordModal';
import { PermissionsPopover } from './PermissionsPopover';
import { notify } from '../notices';

const USERS_QUERY_KEY = ['users'] as const;

/** Правка доступа: готовая — роль, включение — или функцией от учётки, какой её знает
 *  страница, когда до правки дошла очередь, — точечные права (`PermissionsPopover`). */
type Patch = AccessPatch | ((latest: UserCard) => AccessPatch);

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

  /** Учётка, какой её знает страница сейчас, а не какой её видел щелчок. */
  const latest = (user: UserCard): UserCard =>
    queryClient.getQueryData<UserCard[]>(USERS_QUERY_KEY)?.find((one) => one.id === user.id) ??
    user;

  const change = useMutation({
    // Правки — по одной, в порядке щелчков, и точечные права собираются, когда до правки
    // дошла очередь: две разом уходили наборами из одного списка, и вторая затирала первую
    // (QA 10.10.2026). Отказ сервера списка не трогает — переключатель стоит, как стоял.
    scope: { id: 'users-access' },
    mutationFn: ({ user, patch }: { user: UserCard; patch: Patch }) =>
      patchUser(user.id, typeof patch === 'function' ? patch(latest(user)) : patch),
    onSuccess: async (updated) => {
      // Ответ сервера — последнее известное состояние учётки: следующая правка в очереди
      // собирается из него, даже если перечитать список не выйдет.
      queryClient.setQueryData<UserCard[]>(USERS_QUERY_KEY, (list) =>
        list?.map((one) => (one.id === updated.id ? updated : one)),
      );
      await queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY });
      // Правка своих прав меняет то, что человек видит прямо сейчас,
      // — карточку себя надо перечитать, иначе меню останется прежним.
      if (updated.id === me?.id) await refresh();
      notify({ message: `Учётка ${updated.email} обновлена`, color: 'green' });
    },
    onError: (failure) =>
      notify({ title: 'Не изменили', message: refusalOf(failure), color: 'red' }),
  });

  const reset = useMutation({
    mutationFn: (id: number) => resetPassword(id),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY });
      setIssued(result);
    },
    onError: (failure) =>
      notify({ title: 'Не сбросили', message: refusalOf(failure), color: 'red' }),
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
    // Отключённая — значком, а не прозрачностью строки: на 0,55 текст падал ниже нормы
    // контраста (аудит экранов 09.10.2026).
    <Table.Tr key={user.id}>
      <Table.Td>
        <Text fw={user.id === me?.id ? 600 : 400}>{user.email}</Text>
        <Group gap={6}>
          {!user.is_active && (
            <Badge size="xs" color="gray">
              отключена
            </Badge>
          )}
          {user.must_change_password && (
            <Badge size="xs" color="yellow">
              не сменил разовый пароль
            </Badge>
          )}
        </Group>
      </Table.Td>
      <Table.Td>
        <Group justify="center">
          {/* По слову «оператор», а не 140 px под одно слово. */}
          <Select
            size="xs"
            w="7.5rem"
            allowDeselect={false}
            aria-label={`Роль ${user.email}`}
            value={user.role}
            data={[
              { value: 'operator', label: 'оператор' },
              { value: 'admin', label: 'админ' },
            ]}
            onChange={(value) =>
              value !== null && change.mutate({ user, patch: { role: value as Role } })
            }
          />
        </Group>
      </Table.Td>
      <Table.Td>
        <Group justify="center">
          <PermissionsPopover
            user={user}
            disabled={change.isPending}
            onChange={(next) =>
              change.mutate({ user, patch: (now) => ({ permissions: next(now.overrides) }) })
            }
          />
        </Group>
      </Table.Td>
      <Table.Td>
        <Group justify="center">
          {/* Отключение — с подтверждением: учётка перестаёт пускать сразу. Включение —
              без: вернуть доступ промахом нельзя навредить. */}
          <ConfirmPopover
            message={`Отключить ${user.email}? Учётка перестанет пускать сразу, даже с непросроченным пропуском.`}
            confirm="Отключить"
            danger
            onConfirm={() => change.mutate({ user, patch: { is_active: false } })}
          >
            {(ask) => (
              <Switch
                aria-label={`Учётка ${user.email} включена`}
                checked={user.is_active}
                onChange={(event) =>
                  event.currentTarget.checked
                    ? change.mutate({ user, patch: { is_active: true } })
                    : ask()
                }
              />
            )}
          </ConfirmPopover>
        </Group>
      </Table.Td>
      <Table.Td>
        <Text size="sm" c="dimmed">
          {user.last_login_at === null ? 'ни разу' : formatDateTime(user.last_login_at)}
        </Text>
      </Table.Td>
      <Table.Td>
        <ConfirmPopover
          message={`Сбросить пароль ${user.email}? Старый перестанет действовать, новый разовый покажется один раз.`}
          confirm="Сбросить"
          danger
          onConfirm={() => reset.mutate(user.id)}
        >
          {(ask) => (
            <Button
              size="compact-sm"
              variant="default"
              className="press"
              loading={reset.isPending && reset.variables === user.id}
              onClick={ask}
            >
              Сбросить пароль
            </Button>
          )}
        </ConfirmPopover>
      </Table.Td>
    </Table.Tr>
  ));

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        {/* Кнопка — сразу за заголовком, как на «Стоп-листе» (10.10.2026). */}
        <Group gap="md" align="center">
          <PageHead
            title="Учётки"
            hint="Учётки не удаляются: вместе с ней ушла бы история действий. Отключённая учётка перестаёт пускать сразу, даже с непросроченным пропуском."
          />
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

      {/* Поля карточки с таблицей — вместе с полем ячейки те же 32 px, что
          у панели сверху: текст соседних карточек начинается с одного места. */}
      <Card className="glass" p="md">
        <ScrollArea className="scrollSlim" type="auto">
          <Table className="dataTable" verticalSpacing="sm" horizontalSpacing="md" miw={760}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Почта</Table.Th>
                <Table.Th>Роль</Table.Th>
                <Table.Th>Права</Table.Th>
                <Table.Th>Включена</Table.Th>
                <Table.Th>Последний вход</Table.Th>
                <Table.Th>Действия</Table.Th>
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
