(function () {
    function initHeader(header) {
        if (!header) {
            return;
        }

        // Mobile Menu Logic (Legacy / Signup only)
        const toggle = header.querySelector('.menu-toggle');
        const nav = header.querySelector('.mypage-nav');
        
        if (toggle && nav) {
            const links = nav.querySelectorAll('a');
            const closeMenu = () => {
                toggle.setAttribute('aria-expanded', 'false');
                nav.classList.remove('open');
                header.classList.remove('menu-open');
            };
            toggle.addEventListener('click', function () {
                const isExpanded = this.getAttribute('aria-expanded') === 'true';
                this.setAttribute('aria-expanded', String(!isExpanded));
                nav.classList.toggle('open', !isExpanded);
                header.classList.toggle('menu-open', !isExpanded);
            });
            links.forEach((link) => {
                link.addEventListener('click', () => {
                    if (window.matchMedia('(max-width: 768px)').matches) {
                        closeMenu();
                    }
                });
            });
            document.addEventListener('click', (event) => {
                if (!header.contains(event.target)) {
                    closeMenu();
                }
            });
            window.addEventListener('resize', () => {
                if (!window.matchMedia('(max-width: 820px)').matches) {
                    closeMenu();
                }
            });
        }

        // Notification Logic (Shared)
        const notificationBell = document.getElementById('notification-bell');
        const notificationPanel = document.getElementById('notification-panel');
        const notificationClose = document.getElementById('notification-close');
        const notificationBadge = document.getElementById('notification-badge');

        if (notificationBell && notificationPanel) {
            const openNotificationPanel = async (e) => {
                e.preventDefault();
                notificationPanel.classList.add('open');
                notificationPanel.setAttribute('aria-hidden', 'false');
                notificationBell.setAttribute('aria-expanded', 'true');
                
                // 通知パネルを開いたときに既読APIを呼び出す
                try {
                    const response = await fetch('/notifications/mark-read', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        credentials: 'same-origin'
                    });
                    
                    if (response.ok) {
                        const data = await response.json();
                        // バッジを更新
                        updateNotificationBadge(data.unread_count);
                    }
                } catch (error) {
                    console.error('Failed to mark notifications as read:', error);
                }
            };

            const closeNotificationPanel = () => {
                notificationPanel.classList.remove('open');
                notificationPanel.setAttribute('aria-hidden', 'true');
                notificationBell.setAttribute('aria-expanded', 'false');
            };

            // 通知バッジを更新する関数
            const updateNotificationBadge = (unreadCount) => {
                if (notificationBadge) {
                    if (unreadCount > 0) {
                        notificationBadge.textContent = unreadCount;
                        notificationBadge.style.display = 'block';
                    } else {
                        notificationBadge.style.display = 'none';
                    }
                }
            };

            notificationBell.addEventListener('click', openNotificationPanel);

            if (notificationClose) {
                notificationClose.addEventListener('click', closeNotificationPanel);
            }

            // Close when clicking outside
            document.addEventListener('click', (event) => {
                if (notificationPanel.classList.contains('open') && 
                    !notificationPanel.contains(event.target) && 
                    !notificationBell.contains(event.target)) {
                    closeNotificationPanel();
                }
            });

            // Close on Escape key
            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape' && notificationPanel.classList.contains('open')) {
                    closeNotificationPanel();
                }
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            document.querySelectorAll('.header').forEach(initHeader);
        });
    } else {
        document.querySelectorAll('.header').forEach(initHeader);
    }
})();
