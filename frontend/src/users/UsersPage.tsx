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
 *
 * **Роль, права, включение, отключение и сброс чужого пароля — с паролем админа**
 * (аудит 20.11.2026, №10): сервер без него этих действий не делает, и каждое идёт через
 * окно «Ваш пароль» (`ConfirmPasswordModal`). Окно же и подтверждает действие: текст называет
 * последствие, отказ сервера показывается в нём.
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
import { Link } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { ROLE_TITLES, ROLES } from '../api/labels';
import type { AccessPatch, OneTimePassword, Role, UserCard } from '../api/types';
import { listUsers, patchUser, resetPassword } from '../api/users';
import { useSession } from '../auth/AuthProvider';
import { PageHead } from '../components/PageHead';
import { formatDateTime } from '../format';
import { ConfirmPasswordModal } from './ConfirmPasswordModal';
import type { Confirming } from './ConfirmPasswordModal';
import { CreateUserModal } from './CreateUserModal';
import { OneTimePasswordModal } from './OneTimePasswordModal';
import { PermissionsPopover } from './PermissionsPopover';
import { notify } from '../notices';

const USERS_QUERY_KEY = ['users'] as const;

/** Правка доступа: готовая — роль, включение — или функцией от учётки, какой её знает
 *  страница, когда до правки дошла очередь, — точечные права (`PermissionsPopover`). */
type Patch = AccessPatch | ((latest: UserCard) => AccessPatch);

/** Окно подтверждения без самого действия: вопрос, последствие и глагол. */
type Asked = Omit<Confirming, 'run'>;

interface PasswordActionProps {
  /** Своя строка: себе пароль не сбрасывают, а меняют со старым. */
  own: boolean;
  resetting: boolean;
  onReset: () => void;
}

/**
 * Пароль учётки: чужой — сбросить, свой — сменить.
 *
 * Сброс закрывает открытые сеансы учётки (проверка прода 10.10.2026: окно обещало «старый
 * перестанет действовать», а пропуск жил до конца суток). Сброс себе закрыл бы и этот
 * сеанс раньше, чем окно покажет разовый, — сервер его не принимает, и кнопки нет. Чужой
 * сброс спрашивает пароль админа (`ConfirmPasswordModal`), и это окно его подтверждает.
 */
function PasswordAction({ own, resetting, onReset }: PasswordActionProps) {
  if (own) {
    return (
      <Button component={Link} to="/password" size="compact-sm" variant="default" className="press">
        Сменить пароль
      </Button>
    );
  }
  return (
    <Button
      size="compact-sm"
      variant="default"
      className="press"
      loading={resetting}
      onClick={onReset}
    >
      Сбросить пароль
    </Button>
  );
}

export function UsersPage() {
  const queryClient = useQueryClient();
  const { user: me, refresh } = useSession();
  const [creating, setCreating] = useState(false);
  const [issued, setIssued] = useState<OneTimePassword | null>(null);
  const [confirming, setConfirming] = useState<Confirming | null>(null);

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
    // (QA 10.10.2026). Отказ сервера списка не трогает — переключатель стоит, как стоял, а
    // текст отказа показывает окно подтверждения.
    scope: { id: 'users-access' },
    mutationFn: ({ user, patch, password }: { user: UserCard; patch: Patch; password: string }) =>
      patchUser(user.id, typeof patch === 'function' ? patch(latest(user)) : patch, password),
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
  });

  const reset = useMutation({
    mutationFn: ({ id, password }: { id: number; password: string }) => resetPassword(id, password),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: USERS_QUERY_KEY });
      setIssued(result);
    },
  });

  /** Правка доступа — после пароля админа в окне подтверждения. */
  const askChange = (user: UserCard, patch: Patch, asked: Asked) =>
    setConfirming({ ...asked, run: (password) => change.mutateAsync({ user, patch, password }) });

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
          {/* По самой длинной подписи — «Рекламодатели». */}
          <Select
            size="xs"
            w="9.5rem"
            allowDeselect={false}
            aria-label={`Роль ${user.email}`}
            value={user.role}
            data={ROLES.map((role) => ({ value: role, label: ROLE_TITLES[role] }))}
            onChange={(value) =>
              value !== null &&
              value !== user.role &&
              askChange(
                user,
                { role: value as Role },
                {
                  question: `Сменить роль ${user.email}?`,
                  consequence: `${ROLE_TITLES[user.role]} → ${ROLE_TITLES[value as Role]}: права учётки сменятся сразу.`,
                  confirm: 'Сменить роль',
                },
              )
            }
          />
        </Group>
      </Table.Td>
      <Table.Td>
        <Group justify="center">
          <PermissionsPopover
            user={user}
            disabled={change.isPending}
            onChange={(next, words) =>
              askChange(user, (now) => ({ permissions: next(now.overrides) }), {
                question: `Изменить права ${user.email}?`,
                consequence: words,
                confirm: 'Изменить',
              })
            }
          />
        </Group>
      </Table.Td>
      <Table.Td>
        <Group justify="center">
          {/* И отключение, и включение — с паролем админа: сервер без него не примет ни то,
              ни другое (аудит 20.11.2026). Отключение — красной кнопкой: учётка перестаёт
              пускать сразу. */}
          <Switch
            aria-label={`Учётка ${user.email} включена`}
            checked={user.is_active}
            onChange={(event) =>
              event.currentTarget.checked
                ? askChange(
                    user,
                    { is_active: true },
                    {
                      question: `Включить ${user.email}?`,
                      consequence: 'Учётка снова пустит по своему паролю, с правами своей роли.',
                      confirm: 'Включить',
                    },
                  )
                : askChange(
                    user,
                    { is_active: false },
                    {
                      question: `Отключить ${user.email}?`,
                      consequence:
                        'Учётка перестанет пускать сразу, даже с непросроченным пропуском.',
                      confirm: 'Отключить',
                      danger: true,
                    },
                  )
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
        <PasswordAction
          own={user.id === me?.id}
          resetting={reset.isPending && reset.variables.id === user.id}
          onReset={() =>
            setConfirming({
              question: `Сбросить пароль ${user.email}?`,
              consequence:
                'Старый пароль и открытые сеансы перестанут действовать, новый разовый покажется один раз.',
              confirm: 'Сбросить',
              danger: true,
              run: (password) => reset.mutateAsync({ id: user.id, password }),
            })
          }
        />
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
            hint="Учётки не удаляются: вместе с ней ушла бы история действий. Отключённая учётка перестаёт пускать сразу, даже с непросроченным пропуском; сброс пароля так же закрывает её открытые сеансы. Роль, права, включение и сброс пароля подтверждаются вашим паролем."
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
      <ConfirmPasswordModal confirming={confirming} onClose={() => setConfirming(null)} />
      <OneTimePasswordModal issued={issued} onClose={() => setIssued(null)} />
    </Stack>
  );
}
